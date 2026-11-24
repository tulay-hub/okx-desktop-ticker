#!/usr/bin/python3
"""Transparent OKX NVDA perpetual desktop ticker using public market data."""

import argparse
import concurrent.futures
import ctypes
from decimal import Decimal, InvalidOperation
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import signal
import sys
import time
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PyQt5.QtCore import QEvent, QPoint, QPointF, QRectF, QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import (
    QActionGroup, QApplication, QComboBox, QDialog, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QToolButton,
    QVBoxLayout, QWidget,
)

APP_DIR = Path(__file__).resolve().parent
API_BASE = "https://openapi.okx.com"
INSTRUMENT = "NVDA-USDT-SWAP"
ANTHROPIC = "ANTHROPIC-USDT-SWAP"
COMPACT_INSTRUMENTS = (ANTHROPIC, INSTRUMENT)
GREEN = "#7ce5bc"
RED = "#ff8f9c"
MUTED = "#b0b9c9"
PRESETS = {
    "半导体": ("NVDA", "AMD", "MU", "INTC", "TSM", "AVGO", "ARM", "ASML", "SNDK",
               "SMH", "SOXX", "AMAT", "LRCX", "KLAC", "QCOM", "MRVL", "TXN", "ON"),
    "AI 科技": ("ANTHROPIC", "NVDA", "MSFT", "GOOGL", "META", "AMZN", "PLTR", "ORCL", "AMD", "AVGO"),
    "加密货币": ("BTC", "ETH", "SOL", "OKB", "XRP", "DOGE", "ADA", "BNB", "LINK"),
}
DISPLAY_NAMES = {
    "NVDA": "英伟达", "AMD": "超威半导体", "MU": "美光", "INTC": "英特尔",
    "TSM": "台积电", "AVGO": "博通", "ARM": "安谋", "ASML": "阿斯麦",
    "SNDK": "闪迪", "SMH": "半导体 ETF", "SOXX": "半导体 ETF",
    "MSFT": "微软", "GOOGL": "谷歌", "META": "Meta", "AMZN": "亚马逊",
    "PLTR": "Palantir", "ORCL": "甲骨文", "BTC": "比特币", "ETH": "以太坊",
    "SOL": "Solana", "OKB": "欧易平台币", "XRP": "瑞波币", "DOGE": "狗狗币",
    "ADA": "Cardano", "BNB": "币安币", "LINK": "Chainlink", "ANTHROPIC": "Anthropic（Claude）",
}


def display_symbol(instrument):
    return "ANT" if instrument == ANTHROPIC else instrument.split("-")[0]


class InstrumentDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.selected_instrument = None
        self.setWindowTitle("切换行情品种")
        self.resize(440, 510)
        self.setStyleSheet(
            "QDialog {background:#121c2c; color:#edf3fb;}"
            "QLineEdit,QComboBox {background:#202d42; color:#edf3fb; border:1px solid #34435b;"
            "border-radius:6px; padding:9px;}"
            "QListWidget {background:#121c2c; color:#edf3fb; border:none; outline:none;}"
            "QListWidget::item {padding:11px; border-radius:5px;}"
            "QListWidget::item:selected {background:#304561;}"
            "QPushButton {background:#7ce5bc; color:#10231c; border:0; padding:10px; border-radius:6px;}"
            "QPushButton:disabled {background:#334053; color:#8c9aad;}"
            "QLabel {color:#b0b9c9;}"
        )
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索代码或名称，例如 NVDA、ANT、英伟达")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.category = QComboBox()
        self.category.addItems(["自选", *PRESETS, "全部"])
        self.category.setCurrentText("半导体")
        layout.addWidget(self.category)
        self.items = QListWidget()
        layout.addWidget(self.items)
        self.catalog_status = QLabel()
        layout.addWidget(self.catalog_status)
        self.choose_button = QPushButton("添加到自选并切换")
        self.choose_button.setEnabled(False)
        layout.addWidget(self.choose_button)
        self.search.textChanged.connect(self.refresh_items)
        self.category.currentTextChanged.connect(self.refresh_items)
        self.items.itemSelectionChanged.connect(self.update_button)
        self.items.itemDoubleClicked.connect(lambda item: self.choose())
        self.choose_button.clicked.connect(self.choose)
        owner.catalog_updated.connect(self.refresh_items)
        self.refresh_items()

    def refresh_items(self):
        selected = self.items.currentItem()
        selected_id = selected.data(Qt.UserRole) if selected else None
        self.items.clear()
        catalog = self.owner.catalog
        query = self.search.text().strip().upper().replace("-", "").replace("/", "")
        category = self.category.currentText()
        if query or category == "全部":
            candidates = sorted(catalog)
        elif category == "自选":
            candidates = [item for item in self.owner.watchlist if item in catalog]
        else:
            candidates = [f"{symbol}-USDT-SWAP" for symbol in PRESETS[category]
                          if f"{symbol}-USDT-SWAP" in catalog]
        if query and ANTHROPIC in candidates:
            candidates.remove(ANTHROPIC)
            candidates.insert(0, ANTHROPIC)
        for instrument in candidates:
            symbol = display_symbol(instrument)
            name = DISPLAY_NAMES.get(instrument.split("-")[0], "")
            text = f"{symbol}USDT  {name}"
            haystack = f"{instrument.replace('-', '')} {text.upper()}"
            if instrument == ANTHROPIC:
                haystack += " 克劳德"
            if query and query not in haystack:
                continue
            prefix = "★  " if instrument in self.owner.watchlist else "    "
            item = QListWidgetItem(prefix + text)
            item.setData(Qt.UserRole, instrument)
            item.setToolTip(instrument)
            self.items.addItem(item)
            if instrument == selected_id:
                self.items.setCurrentItem(item)
        if self.items.count() == 0:
            text = "没有匹配的 OKX USDT 永续合约" if self.owner.catalog_loaded else "正在加载合约列表…"
            item = QListWidgetItem(text)
            item.setFlags(Qt.NoItemFlags)
            self.items.addItem(item)
        status = f"OKX · {len(catalog)} 个 USDT 永续合约" if self.owner.catalog_loaded else self.owner.catalog_status
        self.catalog_status.setText(status)
        self.update_button()

    def update_button(self):
        item = self.items.currentItem()
        self.choose_button.setEnabled(bool(item and item.data(Qt.UserRole)))

    def choose(self):
        item = self.items.currentItem()
        if item is None or not item.data(Qt.UserRole):
            return
        self.selected_instrument = item.data(Qt.UserRole)
        self.accept()


def request_topmost(window_id, enabled):
    """Ask the X11 window manager after mapping, when GNOME accepts state changes."""
    class MessageData(ctypes.Union):
        _fields_ = [("values", ctypes.c_long * 5), ("bytes", ctypes.c_char * 20)]

    class ClientMessage(ctypes.Structure):
        _fields_ = [
            ("type", ctypes.c_int), ("serial", ctypes.c_ulong),
            ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
            ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
            ("format", ctypes.c_int), ("data", MessageData),
        ]

    class Event(ctypes.Union):
        _fields_ = [("message", ClientMessage), ("padding", ctypes.c_long * 24)]

    x11 = ctypes.CDLL("libX11.so.6")
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                              ctypes.c_long, ctypes.POINTER(Event)]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    display = x11.XOpenDisplay(None)
    if not display:
        return
    try:
        event = Event()
        event.message.type = 33  # ClientMessage
        event.message.display = display
        event.message.window = window_id
        event.message.message_type = x11.XInternAtom(display, b"_NET_WM_STATE", 0)
        event.message.format = 32
        event.message.data.values[0] = 1 if enabled else 0
        event.message.data.values[1] = x11.XInternAtom(display, b"_NET_WM_STATE_ABOVE", 0)
        event.message.data.values[3] = 1  # Normal application request.
        x11.XSendEvent(display, x11.XDefaultRootWindow(display), 0,
                       (1 << 20) | (1 << 19), ctypes.byref(event))
        x11.XFlush(display)
    finally:
        x11.XCloseDisplay(display)


def get_market_data(path):
    proxies = urllib.request.getproxies()
    if not proxies.get("https"):
        # Desktop launchers do not inherit the terminal's proxy environment.
        # urllib does not read GNOME's proxy settings by itself.
        try:
            from gi.repository import Gio
            settings = Gio.Settings.new("org.gnome.system.proxy")
            if settings.get_string("mode") == "manual":
                for protocol in ("https", "http"):
                    proxy_settings = settings.get_child(protocol)
                    host = proxy_settings.get_string("host").strip()
                    port = proxy_settings.get_int("port")
                    if host and port > 0:
                        if ":" in host and not host.startswith("["):
                            host = f"[{host}]"
                        proxies["https"] = f"http://{host}:{port}"
                        break
        except (ImportError, ValueError, RuntimeError):
            pass
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    request = urllib.request.Request(
        API_BASE + path,
        headers={"User-Agent": "NVDA-Desktop-Widget/1.0", "Accept": "application/json",
                 "Cache-Control": "no-cache"},
    )
    with opener.open(request, timeout=6) as response:
        payload = json.load(response)
    if payload.get("code") != "0" or not payload.get("data"):
        raise ValueError("OKX 未返回有效行情")
    return payload["data"]


def get_ticker(instrument):
    data = get_market_data(f"/api/v5/market/ticker?instId={instrument}")[0]
    if data.get("instId") != instrument:
        raise ValueError("合约代码不匹配")
    values = [float(data[key]) for key in ("last", "open24h", "high24h", "low24h")]
    if not all(math.isfinite(value) and value > 0 for value in values):
        raise ValueError("行情价格无效")
    if int(data["ts"]) <= 0:
        raise ValueError("行情时间无效")
    return data


class MarketWorker(QThread):
    ticker = pyqtSignal(dict)
    candles = pyqtSignal(str, list)
    failed = pyqtSignal(str, str)
    catalog_ready = pyqtSignal(list)
    catalog_failed = pyqtSignal(str)

    def __init__(self, instrument, parent=None):
        super().__init__(parent)
        self.instrument = instrument
        self.compact = False

    def run(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            candle_request = None
            candle_instrument = None
            catalog_request = None
            next_catalog = 0.0
            next_candles = 0.0
            ticker_requests = {}
            next_ticker = {}
            instrument = self.instrument
            while not self.isInterruptionRequested():
                now = time.monotonic()
                if instrument != self.instrument:
                    instrument = self.instrument
                    next_candles = 0.0
                if catalog_request is not None and catalog_request.done():
                    try:
                        self.catalog_ready.emit(catalog_request.result())
                        next_catalog = now + 600
                    except Exception:
                        self.catalog_failed.emit("合约列表连接失败，正在重试")
                        next_catalog = now + 10
                    catalog_request = None
                if catalog_request is None and now >= next_catalog:
                    catalog_request = pool.submit(
                        get_market_data, "/api/v5/public/instruments?instType=SWAP",
                    )
                if candle_request is not None and candle_request.done():
                    try:
                        self.candles.emit(candle_instrument, candle_request.result())
                        next_candles = now + 30 if candle_instrument == instrument else 0
                    except Exception:
                        next_candles = now + 10 if candle_instrument == instrument else 0
                    candle_request = None
                if candle_request is None and now >= next_candles:
                    candle_instrument = instrument
                    candle_request = pool.submit(
                        get_market_data,
                        f"/api/v5/market/candles?instId={instrument}&bar=1m&limit=120",
                    )
                watched = set(COMPACT_INSTRUMENTS) if self.compact else {instrument}
                for symbol, request in list(ticker_requests.items()):
                    if not request.done():
                        continue
                    del ticker_requests[symbol]
                    if symbol not in watched:
                        next_ticker.pop(symbol, None)
                        continue
                    try:
                        self.ticker.emit(request.result())
                    except Exception as error:
                        self.failed.emit(symbol, f"{type(error).__name__}: {error}")
                    next_ticker[symbol] = max(next_ticker[symbol], now + 0.2)
                next_ticker = {symbol: due for symbol, due in next_ticker.items()
                               if symbol in watched or symbol in ticker_requests}
                for symbol in watched:
                    if symbol not in ticker_requests and now >= next_ticker.get(symbol, 0):
                        ticker_requests[symbol] = pool.submit(get_ticker, symbol)
                        next_ticker[symbol] = now + 2
                self.msleep(100)


class CandleChart(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(170)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.history = {}
        self.live_bars = {}
        self.hover_position = None
        self.price_decimals = 2

    def clear(self):
        self.history.clear()
        self.live_bars.clear()
        self.hover_position = None
        self.update()

    def set_history(self, rows):
        history = {}
        for row in rows:
            try:
                timestamp = int(row[0])
                opening, high, low, closing = map(float, row[1:5])
                volume = float(row[5])
                if timestamp <= 0 or not all(
                    math.isfinite(value) and value > 0 for value in (opening, high, low, closing)
                ):
                    continue
                if high < max(opening, closing) or low > min(opening, closing):
                    continue
                if not math.isfinite(volume) or volume < 0:
                    continue
                history[timestamp] = {
                    "timestamp": timestamp, "open": opening, "high": high, "low": low,
                    "close": closing, "volume": volume,
                    "confirmed": row[-1] == "1", "estimated_open": False,
                }
            except (ValueError, IndexError, TypeError):
                continue
        if history:
            self.history = dict(sorted(history.items())[-120:])
            for timestamp, candle in self.history.items():
                if candle["confirmed"]:
                    self.live_bars.pop(timestamp, None)
            self.update()

    def set_price(self, timestamp, price):
        minute = timestamp // 60000 * 60000
        candle = self.live_bars.get(minute)
        if candle is None:
            candle = {
                "timestamp": minute, "open": price, "high": price, "low": price,
                "close": price, "volume": None, "confirmed": False,
                "estimated_open": True,
            }
            self.live_bars[minute] = candle
        candle["high"] = max(candle["high"], price)
        candle["low"] = min(candle["low"], price)
        candle["close"] = price
        self.live_bars = dict(sorted(self.live_bars.items())[-120:])
        self.update()

    def bars(self):
        candles = {timestamp: dict(bar) for timestamp, bar in self.history.items()}
        for timestamp, live in self.live_bars.items():
            if timestamp not in candles:
                candles[timestamp] = dict(live)
            elif not candles[timestamp]["confirmed"]:
                candle = candles[timestamp]
                candle["high"] = max(candle["high"], live["high"])
                candle["low"] = min(candle["low"], live["low"])
                candle["close"] = live["close"]
        return [bar for _, bar in sorted(candles.items())[-120:]]

    def points(self):
        return [(bar["timestamp"], bar["close"]) for bar in self.bars()]

    def plot_rect(self):
        return QRectF(2, 7, max(1, self.width() - 58), self.height() - 29)

    def hovered_bar(self):
        bars = self.bars()
        rect = self.plot_rect()
        if not bars or self.hover_position is None or not rect.contains(self.hover_position):
            return None
        index = min(len(bars) - 1, int((self.hover_position.x() - rect.left()) / rect.width() * len(bars)))
        return index, bars[index]

    def mouseMoveEvent(self, event):
        if event.buttons():
            event.ignore()
            return
        self.hover_position = QPointF(event.pos())
        self.update()
        event.accept()

    def leaveEvent(self, event):
        self.hover_position = None
        self.update()
        super().leaveEvent(event)

    def set_tick_size(self, tick_size):
        try:
            tick = Decimal(str(tick_size))
            if not tick.is_finite() or tick <= 0:
                raise ValueError("Invalid price tick")
            self.price_decimals = max(0, -tick.normalize().as_tuple().exponent)
        except (InvalidOperation, ValueError, TypeError):
            self.price_decimals = 2
        self.update()

    def price_text(self, value):
        return f"{value:,.{self.price_decimals}f}"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # A nearly invisible hit surface keeps hover usable with a fully clear background.
        painter.fillRect(self.rect(), QColor(15, 23, 37, 1))
        painter.setFont(QFont("Inter", 8))
        rect = self.plot_rect()
        bars = self.bars()
        if not bars:
            painter.setPen(QColor(MUTED))
            painter.drawText(rect, Qt.AlignCenter, "正在读取 K 线…")
            return
        low = min(bar["low"] for bar in bars)
        high = max(bar["high"] for bar in bars)
        span = max(high - low, max(abs(high), 1) * 0.0001)
        padding = span * 0.08
        low, high = low - padding, high + padding

        def price_y(price):
            return rect.bottom() - (price - low) / (high - low) * rect.height()

        for fraction in (0.1, 0.5, 0.9):
            y = rect.top() + rect.height() * fraction
            painter.setPen(QPen(QColor(210, 220, 235, 35), 1, Qt.DotLine))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QColor(MUTED))
            value = high - (high - low) * fraction
            painter.drawText(QRectF(rect.right() + 5, y - 7, 51, 14),
                             Qt.AlignVCenter | Qt.AlignLeft, self.price_text(value))
        spacing = rect.width() / len(bars)
        body_width = max(1.2, min(10, spacing * 0.68))
        for index, candle in enumerate(bars):
            x = rect.left() + (index + 0.5) * spacing
            color = QColor(GREEN if candle["close"] >= candle["open"] else RED)
            painter.setPen(QPen(color, 1))
            painter.drawLine(QPointF(x, price_y(candle["high"])), QPointF(x, price_y(candle["low"])))
            top, bottom = sorted((price_y(candle["open"]), price_y(candle["close"])))
            painter.fillRect(QRectF(x - body_width / 2, top, body_width, max(1.2, bottom - top)), color)
        for index, alignment in ((0, Qt.AlignLeft), (len(bars) // 2, Qt.AlignHCenter),
                                 (len(bars) - 1, Qt.AlignRight)):
            x = rect.left() + (index + 0.5) * spacing
            x = max(rect.left(), min(rect.right() - 46, x - 23))
            painter.setPen(QColor(MUTED))
            painter.drawText(QRectF(x, rect.bottom() + 5, 46, 16), alignment,
                             time.strftime("%H:%M", time.localtime(bars[index]["timestamp"] / 1000)))
        selection = self.hovered_bar()
        if selection is None:
            return
        index, candle = selection
        x = rect.left() + (index + 0.5) * spacing
        y = self.hover_position.y()
        painter.setPen(QPen(QColor(225, 234, 246, 180), 1, Qt.DashLine))
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        card_width = min(264, rect.width() - 8)
        card_x = rect.left() + 5 if x > rect.center().x() else rect.right() - card_width - 5
        card = QRectF(card_x, rect.top() + 5, card_width, 111)
        painter.setPen(QPen(QColor(130, 153, 180, 120), 1))
        painter.setBrush(QColor(18, 28, 44, 245))
        painter.drawRoundedRect(card, 7, 7)
        state = "已收盘" if candle["confirmed"] else "未收盘"
        if candle["estimated_open"]:
            state = "实时估算，待 K 线同步"
        timestamp = time.strftime("%m/%d %H:%M", time.localtime(candle["timestamp"] / 1000))
        change = (candle["close"] / candle["open"] - 1) * 100
        volume = "待同步" if candle["volume"] is None else f"{candle['volume']:,.2f} 张"
        lines = [
            f"{timestamp} · {state}",
            f"开 {self.price_text(candle['open'])}    高 {self.price_text(candle['high'])}",
            f"低 {self.price_text(candle['low'])}    收 {self.price_text(candle['close'])}",
            f"成交量 {volume}",
            f"涨跌 {change:+.2f}%",
        ]
        painter.setFont(QFont("Inter", 9))
        for line_number, text in enumerate(lines):
            color = GREEN if change >= 0 else RED
            painter.setPen(QColor(color if line_number == 4 else "#e8eff8"))
            painter.drawText(QRectF(card.left() + 9, card.top() + 7 + line_number * 19,
                                   card.width() - 18, 19), Qt.AlignVCenter | Qt.AlignLeft, text)


class CompactTicker(QWidget):
    expand_requested = pyqtSignal(str)

    def __init__(self, settings):
        super().__init__(flags=Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.settings = settings
        self.drag_start = None
        self.dragging = False
        self.installEventFilter(self)
        self.setWindowTitle("OKX 精简行情")
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.quotes = {}
        self.errors = set()
        self.entries = {}
        self.setObjectName("compactTicker")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(
            "QWidget#compactTicker {background:#121c2c; border:1px solid #283a51;}"
            "QPushButton {background:transparent; color:#eef3fb; border:0; border-radius:4px;}"
            "QPushButton:hover,QPushButton:focus {background:#253750;}"
            "QLabel {color:#eef3fb;}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)
        expand = QPushButton("▤")
        expand.setFixedSize(26, 26)
        expand.setToolTip("点击展开当前品种 K 线，按住拖动行情条")
        expand.setAccessibleName("展开当前品种 K 线")
        expand.installEventFilter(self)
        expand.clicked.connect(lambda: self.expand_requested.emit(""))
        layout.addWidget(expand)
        for instrument in COMPACT_INSTRUMENTS:
            button = QPushButton()
            button.setFixedSize(164, 26)
            button.setAccessibleName(f"展开 {display_symbol(instrument)} K 线")
            button.installEventFilter(self)
            row = QHBoxLayout(button)
            row.setContentsMargins(6, 0, 6, 0)
            row.setSpacing(5)
            symbol = QLabel(display_symbol(instrument))
            symbol.setFont(QFont("Inter", 9, QFont.DemiBold))
            price = QLabel("—")
            change = QLabel("—")
            for label in (symbol, price, change):
                label.setAttribute(Qt.WA_TransparentForMouseEvents)
                row.addWidget(label)
            row.addStretch()
            button.clicked.connect(lambda checked, code=instrument: self.expand_requested.emit(code))
            self.entries[instrument] = (button, price, change)
            layout.addWidget(button)
        self.setFixedSize(self.sizeHint())
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.place_strip()
        QTimer.singleShot(150, self.place_strip)

    def place_strip(self):
        if not self.isVisible():
            return
        if self.drag_start is None:
            position = self.settings.value("compact_position", type=QPoint)
            screen = (QApplication.screenAt(position + self.rect().center())
                      if position is not None else None)
            if screen is None:
                area = QApplication.primaryScreen().availableGeometry()
                position = area.topLeft() + QPoint(8, 5)
            else:
                area = screen.availableGeometry()
            position.setX(max(area.left(), min(position.x(), area.right() - self.width() + 1)))
            position.setY(max(area.top(), min(position.y(), area.bottom() - self.height() + 1)))
            self.move(position)
        if QApplication.platformName() == "xcb":
            request_topmost(int(self.winId()), True)

    def eventFilter(self, watched, event):
        event_type = event.type()
        if event_type in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick) and event.button() == Qt.LeftButton:
            self.drag_start = (event.globalPos(), self.pos())
            self.dragging = False
        elif event_type == QEvent.MouseMove and self.drag_start is not None and event.buttons() & Qt.LeftButton:
            cursor_start, window_start = self.drag_start
            delta = event.globalPos() - cursor_start
            if not self.dragging and delta.manhattanLength() >= QApplication.startDragDistance():
                self.dragging = True
                if isinstance(watched, QPushButton):
                    watched.setDown(False)
            if self.dragging:
                self.move(window_start + delta)
                return True
        elif event_type == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            self.drag_start = None
            if self.dragging:
                self.dragging = False
                self.settings.setValue("compact_position", self.pos())
                self.settings.sync()
                return True
        return super().eventFilter(watched, event)

    def receive_quote(self, instrument, quote):
        previous = self.quotes.get(instrument)
        if previous is None or int(quote["ts"]) >= int(previous["ts"]):
            self.quotes[instrument] = quote
            self.errors.discard(instrument)

    def refresh(self):
        for instrument, (button, price, change) in self.entries.items():
            quote = self.quotes.get(instrument)
            status = "正在连接"
            color = MUTED
            change_text = "—"
            if quote:
                price.setText(f"{float(quote['last']):,.2f}")
                percent = (float(quote["last"]) / float(quote["open24h"]) - 1) * 100
                color = GREEN if percent >= 0 else RED
                arrow = "▲" if percent >= 0 else "▼"
                change_text = f"{arrow}{abs(percent):.1f}%"
                status = "行情延迟" if time.time() - int(quote["ts"]) / 1000 > 15 else "实时"
            if instrument in self.errors:
                status = "连接中断"
            if status in ("行情延迟", "连接中断"):
                change_text, color = "延迟" if status == "行情延迟" else "断线", "#f5cf84"
            change.setText(change_text)
            change.setStyleSheet(f"color:{color};")
            button.setToolTip(f"点击展开 {display_symbol(instrument)} K 线 · {status} · 24 小时涨跌幅\n按住拖动行情条")

    def closeEvent(self, event):
        event.accept()
        self.expand_requested.emit("")


class TickerWindow(QWidget):
    catalog_updated = pyqtSignal()

    def __init__(self, smoke_report=None, snapshot=None):
        super().__init__()
        self.settings = QSettings(str(APP_DIR / "settings.ini"), QSettings.IniFormat)
        self.background_alpha = max(0, min(240, int(self.settings.value("background_alpha", 160))))
        self.instrument = self.settings.value("instrument", INSTRUMENT, type=str)
        self.watchlist = self.settings.value("watchlist", [INSTRUMENT], type=list)
        self.catalog = {self.instrument: {"instId": self.instrument}}
        self.catalog_loaded = False
        self.catalog_status = "正在加载 OKX 合约列表…"
        self.latest_quote_ms = 0
        self.received_at = 0.0
        self.refresh_count = 0
        self.quote_updates = 0
        self.quote_samples = []
        self.network_error = False
        self.last_error = ""
        self.drag_offset = None
        self.initial_placement_pending = True
        self.smoke_report = smoke_report
        self.snapshot_path = snapshot
        self.smoke_started = time.monotonic()
        self.setWindowTitle(f"{display_symbol(self.instrument)}USDT 实时悬浮行情")
        self.setObjectName("nvdaTicker")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_QuitOnClose, True)
        self.setFixedSize(500, 350)
        self.setStyleSheet("QLabel {background: transparent; color: #f4f7fb;}"
                           "QToolButton {background: transparent; border: none; color: #b0b9c9;"
                           "font-size: 18px; border-radius: 6px;}"
                           "QToolButton:hover {background: rgba(255,255,255,28); color: white;}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 15, 20, 13)
        layout.setSpacing(5)
        header = QHBoxLayout()
        self.symbol_button = QToolButton()
        self.symbol_button.setText(display_symbol(self.instrument) + "USDT  ▾")
        self.symbol_button.setStyleSheet(
            "QToolButton {font-size:14px; font-weight:600; color:#f4f7fb; padding:2px 5px;}"
            "QToolButton:hover {background:rgba(255,255,255,28);}"
        )
        self.symbol_button.setToolTip("切换板块、搜索品种、添加到自选")
        self.symbol_button.clicked.connect(self.open_selector)
        header.addWidget(self.symbol_button)
        header.addWidget(self.label("永续", 9, MUTED))
        header.addStretch()
        menu_button = QToolButton()
        menu_button.setText("⋮")
        menu_button.setFixedSize(24, 24)
        menu_button.setToolTip("透明度与置顶设置，也可以右键打开")
        menu_button.clicked.connect(lambda: self.show_menu(menu_button.mapToGlobal(QPoint(0, 24))))
        header.addWidget(menu_button)
        self.compact_button = QToolButton()
        self.compact_button.setText("−")
        self.compact_button.setFixedSize(24, 24)
        self.compact_button.setToolTip("缩成顶部行情条")
        self.compact_button.clicked.connect(self.show_compact)
        header.addWidget(self.compact_button)
        close_button = QToolButton()
        close_button.setText("×")
        close_button.setFixedSize(24, 24)
        close_button.setToolTip("关闭悬浮窗")
        close_button.clicked.connect(self.close)
        header.addWidget(close_button)
        layout.addLayout(header)
        prices = QHBoxLayout()
        self.price_label = self.label("—", 30, "#f4f7fb", QFont.DemiBold)
        prices.addWidget(self.price_label)
        prices.addWidget(self.label("USDT", 9, MUTED), alignment=Qt.AlignBottom)
        prices.addStretch()
        self.change_label = self.label("—", 10, MUTED, QFont.DemiBold)
        self.change_label.setToolTip("最近 24 小时涨跌幅")
        prices.addWidget(self.change_label, alignment=Qt.AlignBottom)
        layout.addLayout(prices)
        self.range_label = self.label("24h 高 —    低 —", 9, MUTED)
        layout.addWidget(self.range_label)
        self.chart = CandleChart()
        layout.addWidget(self.chart)
        chart_caption = QHBoxLayout()
        chart_caption.addWidget(self.label("1 分钟 K 线 · 近 2 小时", 8, MUTED))
        chart_caption.addStretch()
        chart_caption.addWidget(self.label("OKX", 8, MUTED))
        layout.addLayout(chart_caption)
        footer = QHBoxLayout()
        self.status_label = self.label("● 正在连接…", 8, "#f5cf84")
        footer.addWidget(self.status_label)
        footer.addStretch()
        footer.addWidget(self.label("2 秒刷新", 8, MUTED))
        layout.addLayout(footer)
        # Let the containing window receive drag and context-menu events.
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.place_window()
        self.compact_ticker = CompactTicker(self.settings)
        self.compact_ticker.expand_requested.connect(self.restore_chart)
        self.worker = MarketWorker(self.instrument, self)
        self.worker.ticker.connect(self.receive_ticker)
        self.worker.candles.connect(self.receive_candles)
        self.worker.failed.connect(self.receive_error)
        self.worker.catalog_ready.connect(self.receive_catalog)
        self.worker.catalog_failed.connect(self.catalog_error)
        self.worker.start()
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.update_status)
        self.status_timer.start(500)

    @staticmethod
    def label(text, size, color, weight=QFont.Normal):
        label = QLabel(text)
        label.setFont(QFont("Inter", size, weight))
        label.setStyleSheet(f"color: {color}; background: transparent;")
        return label

    def place_window(self):
        available = QApplication.primaryScreen().availableGeometry()
        position = self.settings.value("position", QPoint(available.right() - self.width() - 30,
                                                         available.top() + 45))
        if not isinstance(position, QPoint):
            position = available.topLeft() + QPoint(30, 45)
        if not any(screen.availableGeometry().contains(position + QPoint(40, 20))
                   for screen in QApplication.screens()):
            position = available.topLeft() + QPoint(30, 45)
        for screen in QApplication.screens():
            area = screen.availableGeometry()
            if area.contains(position + QPoint(40, 20)):
                position.setX(max(area.left(), min(position.x(), area.right() - self.width() + 1)))
                position.setY(max(area.top(), min(position.y(), area.bottom() - self.height() + 1)))
                break
        self.move(position)

    def receive_catalog(self, rows):
        self.catalog = {row["instId"]: row for row in rows
                        if row.get("state") == "live" and row.get("settleCcy") == "USDT"}
        self.chart.set_tick_size(self.catalog.get(self.instrument, {}).get("tickSz", "0.01"))
        self.catalog_loaded = True
        self.catalog_updated.emit()

    def catalog_error(self, message):
        self.catalog_status = message
        self.catalog_updated.emit()

    def open_selector(self):
        dialog = InstrumentDialog(self)
        if dialog.exec_() == QDialog.Accepted and dialog.selected_instrument:
            instrument = dialog.selected_instrument
            if instrument not in self.watchlist:
                self.watchlist.append(instrument)
                self.settings.setValue("watchlist", self.watchlist)
            self.select_instrument(instrument)
        dialog.deleteLater()

    def select_instrument(self, instrument):
        if instrument == self.instrument:
            return
        self.instrument = instrument
        self.settings.setValue("instrument", instrument)
        symbol = display_symbol(instrument) + "USDT"
        self.symbol_button.setText(symbol + "  ▾")
        self.setWindowTitle(f"{symbol} 实时悬浮行情")
        self.latest_quote_ms = 0
        self.received_at = 0.0
        self.refresh_count = self.quote_updates = 0
        self.quote_samples.clear()
        self.network_error = False
        self.last_error = ""
        self.price_label.setText("—")
        self.change_label.setText("—")
        self.range_label.setText("24h 高 —    低 —")
        self.chart.clear()
        self.chart.set_tick_size(self.catalog.get(instrument, {}).get("tickSz", "0.01"))
        self.worker.instrument = instrument
        self.update_status()

    def receive_candles(self, instrument, rows):
        if instrument == self.instrument:
            self.chart.set_history(rows)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(15, 23, 37, self.background_alpha))
        painter.setPen(QPen(QColor(211, 224, 246, min(self.background_alpha, 45)), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 18, 18)

    def receive_ticker(self, quote):
        instrument = quote.get("instId")
        if instrument in COMPACT_INSTRUMENTS:
            self.compact_ticker.receive_quote(instrument, quote)
            if self.compact_ticker.isVisible():
                self.compact_ticker.refresh()
        if instrument != self.instrument:
            return
        timestamp = int(quote["ts"])
        if timestamp < self.latest_quote_ms:
            return
        if timestamp > self.latest_quote_ms:
            self.quote_updates += 1
            self.quote_samples.append({"timestamp_ms": timestamp, "price": quote["last"]})
            self.quote_samples = self.quote_samples[-3:]
            if self.quote_updates <= 3:
                logging.info("行情更新 price=%s timestamp_ms=%s", quote["last"], timestamp)
        self.latest_quote_ms = timestamp
        self.received_at = time.monotonic()
        self.refresh_count += 1
        self.network_error = False
        self.last_error = ""
        last, opening = float(quote["last"]), float(quote["open24h"])
        change = (last / opening - 1) * 100
        self.price_label.setText(self.chart.price_text(last))
        self.change_label.setText(f"{change:+.2f}%")
        self.change_label.setStyleSheet(f"color: {GREEN if change >= 0 else RED}; background: transparent;")
        self.range_label.setText(
            f"24h 高 {self.chart.price_text(float(quote['high24h']))}    "
            f"低 {self.chart.price_text(float(quote['low24h']))}"
        )
        self.chart.set_price(timestamp, last)
        self.update_status()

    def receive_error(self, instrument, message):
        if instrument in COMPACT_INSTRUMENTS:
            self.compact_ticker.errors.add(instrument)
        if instrument != self.instrument:
            return
        if not self.network_error:
            logging.warning("行情连接失败 %s", message)
        self.network_error = True
        self.last_error = message
        self.update_status()

    def update_status(self):
        if self.compact_ticker.isVisible():
            self.compact_ticker.refresh()
        age = max(0, time.time() - self.latest_quote_ms / 1000) if self.latest_quote_ms else float("inf")
        updated = time.strftime("%H:%M:%S", time.localtime(self.latest_quote_ms / 1000))
        if self.network_error:
            text, color = "● 连接中断 · 自动重试", "#f5cf84"
        elif self.latest_quote_ms == 0:
            text, color = "● 正在连接…", "#f5cf84"
        elif age > 15 or time.monotonic() - self.received_at > 10:
            text, color = f"● 行情延迟 · {updated}", "#f5cf84"
        else:
            text, color = f"● 实时 · {updated}", GREEN
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; background: transparent;")
        self.status_label.setToolTip(self.last_error or "更新时间为交易所行情时间，本机时区显示")
        if self.smoke_report:
            if self.quote_updates >= 3 and len(self.chart.history) >= 2:
                QTimer.singleShot(200, self.finish_smoke_test)
                self.status_timer.stop()
            elif time.monotonic() - self.smoke_started > 35:
                self.finish_smoke_test()

    def show_menu(self, position):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu {background:#182235; color:#eef2f7; padding:6px;}"
                           "QMenu::item {padding:6px 20px;} QMenu::item:selected {background:#2d3f59;}")
        opacity_menu = menu.addMenu("背景透明度")
        group = QActionGroup(opacity_menu)
        for label, alpha in (("完全透明", 0), ("70% 透明", 77), ("默认半透明", 160), ("接近不透明", 230)):
            action = opacity_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(alpha == self.background_alpha)
            group.addAction(action)
            action.triggered.connect(lambda checked, value=alpha: self.set_background(value))
        top = menu.addAction("保持置顶")
        top.setCheckable(True)
        top.setChecked(bool(self.windowFlags() & Qt.WindowStaysOnTopHint))
        top.triggered.connect(self.toggle_top)
        menu.addSeparator()
        menu.addAction("缩成顶部行情条", self.show_compact)
        menu.addAction("关闭", self.close)
        menu.exec_(position)

    def set_background(self, alpha):
        self.background_alpha = alpha
        self.settings.setValue("background_alpha", alpha)
        self.update()

    def show_compact(self):
        self.settings.setValue("position", self.pos())
        self.worker.compact = True
        self.compact_ticker.refresh()
        self.compact_ticker.show()
        self.hide()

    def restore_chart(self, instrument):
        if instrument:
            self.select_instrument(instrument)
            quote = self.compact_ticker.quotes.get(instrument)
            if quote and instrument not in self.compact_ticker.errors:
                self.receive_ticker(quote)
        self.worker.compact = False
        self.show()
        self.place_window()
        self.raise_()
        self.compact_ticker.hide()

    def toggle_top(self, enabled):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, enabled)
        self.show()

    def showEvent(self, event):
        super().showEvent(event)
        if self.initial_placement_pending:
            self.initial_placement_pending = False
            QTimer.singleShot(150, self.place_window)
        if QApplication.platformName() == "xcb":
            QTimer.singleShot(150, self.sync_topmost)

    def sync_topmost(self):
        if self.isVisible():
            request_topmost(int(self.winId()), bool(self.windowFlags() & Qt.WindowStaysOnTopHint))

    def contextMenuEvent(self, event):
        self.show_menu(event.globalPos())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_offset = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self.drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_offset = None
            self.settings.setValue("position", self.pos())

    def finish_smoke_test(self):
        ready = (self.quote_updates >= 3 and len(self.chart.history) >= 2
                 and time.time() - self.latest_quote_ms / 1000 <= 15)
        if self.snapshot_path:
            self.grab().save(str(self.snapshot_path))
        image = self.grab().toImage()
        report = {
            "passed": ready,
            "instrument": self.instrument,
            "price": self.price_label.text(),
            "quote_timestamp_ms": self.latest_quote_ms,
            "refresh_count": self.refresh_count,
            "distinct_quote_updates": self.quote_updates,
            "recent_quotes": self.quote_samples,
            "chart_points": len(self.chart.points()),
            "status": self.status_label.text(),
            "platform": QApplication.platformName(),
            "window_id": int(self.winId()),
            "geometry": list(self.geometry().getRect()),
            "always_on_top": bool(self.windowFlags() & Qt.WindowStaysOnTopHint),
            "translucent": self.testAttribute(Qt.WA_TranslucentBackground),
            "corner_alpha": image.pixelColor(0, 0).alpha(),
            "background_alpha": image.pixelColor(5, self.height() // 2).alpha(),
        }
        Path(self.smoke_report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        self.smoke_report = None
        self.close()
        QApplication.exit(0 if ready else 1)

    def closeEvent(self, event):
        logging.info("关闭窗口，停止行情请求")
        self.compact_ticker.hide()
        self.status_timer.stop()
        self.worker.requestInterruption()
        self.worker.wait()
        self.settings.setValue("position", self.pos())
        self.settings.sync()
        event.accept()
        QApplication.instance().quit()


def main():
    parser = argparse.ArgumentParser(description="NVDAUSDT 透明桌面行情")
    parser.add_argument("--smoke-test", type=Path, help="读取至少三次行情后写入验证报告并退出")
    parser.add_argument("--snapshot", type=Path, help="验证模式下保存窗口截图")
    args = parser.parse_args()
    logging.basicConfig(filename=APP_DIR / "runtime.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    logging.info("启动行情窗口 pid=%s", os.getpid())
    # Retain the file handle for the process lifetime to prevent duplicate windows.
    lock = (APP_DIR / "runtime.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("NVDAUSDT 悬浮窗已经运行。")
        return 0
    app = QApplication(sys.argv[:1])
    app.setApplicationName("NVDAUSDT Widget")
    app.setOrganizationName("Local Desktop Tools")
    app.setQuitOnLastWindowClosed(True)
    app.setFont(QFont("Inter", 10))
    window = TickerWindow(args.smoke_test, args.snapshot)
    signal.signal(signal.SIGTERM, lambda signum, frame: window.close())
    signal.signal(signal.SIGINT, lambda signum, frame: window.close())
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())

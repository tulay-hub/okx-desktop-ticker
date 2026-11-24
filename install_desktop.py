#!/usr/bin/python3
"""为当前克隆目录创建可直接启动的桌面快捷方式。"""

from pathlib import Path
import subprocess

from gi.repository import Gio, GLib

APP_DIR = Path(__file__).resolve().parent


def install(desktop_dir=None):
    if desktop_dir is None:
        result = subprocess.run(
            ["xdg-user-dir", "DESKTOP"], check=True, capture_output=True, text=True,
        )
        desktop_dir = Path(result.stdout.strip())
    desktop_dir = Path(desktop_dir)
    desktop_dir.mkdir(parents=True, exist_ok=True)
    launcher = str(APP_DIR / "launch.sh")
    for character in (chr(92), chr(34), chr(96), chr(36)):
        launcher = launcher.replace(character, chr(92) + character)
    launcher = launcher.replace("%", "%%")
    entry = GLib.KeyFile()
    values = {
        "Version": "1.0",
        "Type": "Application",
        "Name": "OKX 实时行情",
        "Comment": "透明桌面 K 线、板块预设和品种搜索",
        "Exec": f'/bin/sh "{launcher}"',
        "Icon": "utilities-system-monitor",
        "Categories": "Office;Finance;",
    }
    for key, value in values.items():
        entry.set_string("Desktop Entry", key, value)
    entry.set_boolean("Desktop Entry", "Terminal", False)
    entry.set_boolean("Desktop Entry", "StartupNotify", False)
    destination = desktop_dir / "okx-desktop-ticker.desktop"
    destination.write_text(entry.to_data()[0], encoding="utf-8")
    destination.chmod(0o755)
    try:
        Gio.File.new_for_path(str(destination)).set_attribute_string(
            "metadata::trusted", "true", Gio.FileQueryInfoFlags.NONE, None,
        )
    except GLib.Error:
        print("快捷方式已创建，可在桌面右键选择“允许启动”。")
    print(f"桌面快捷方式已创建：{destination}")
    return destination


if __name__ == "__main__":
    install()

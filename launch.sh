#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export QT_QPA_PLATFORM=xcb
unset QT_PLUGIN_PATH QT_QPA_PLATFORM_PLUGIN_PATH PYTHONHOME PYTHONPATH
exec /usr/bin/python3 "$APP_DIR/widget.py" "$@"

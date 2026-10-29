#!/bin/sh
set -eu
MENU_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP_DIR="$MENU_ROOT/build/RuntimeProcessMenuBar.app"
mkdir -p "$APP_DIR/Contents/MacOS"
xcrun swiftc -O -swift-version 5 -framework AppKit "$MENU_ROOT/main.swift" -o "$APP_DIR/Contents/MacOS/RuntimeProcessMenuBar"
cp "$MENU_ROOT/Info.plist" "$APP_DIR/Contents/Info.plist"
mkdir -p "$APP_DIR/Contents/Resources"
DEFAULTS="$APP_DIR/Contents/Resources/ResidentDefaults.plist"
rm -f "$DEFAULTS"
if [ "$#" -gt 0 ]; then
    /usr/bin/plutil -create xml1 "$DEFAULTS"
    while [ "$#" -gt 0 ]; do
        [ "$#" -ge 2 ] || { echo "引数はkeyと絶対pathの組で指定してください" >&2; exit 2; }
        case "$1" in
            --cli) CONFIG_KEY=cli ;;
            --state) CONFIG_KEY=state ;;
            --threads-db) CONFIG_KEY=threads_db ;;
            --bindings) CONFIG_KEY=bindings ;;
            *) echo "未対応の引数です" >&2; exit 2 ;;
        esac
        case "$2" in /*) ;; *) echo "絶対pathを指定してください" >&2; exit 2 ;; esac
        /usr/bin/plutil -insert "$CONFIG_KEY" -string "$2" "$DEFAULTS"
        shift 2
    done
fi
printf '%s\n' "$APP_DIR"

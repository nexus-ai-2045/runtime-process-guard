#!/bin/sh
set -eu
MENU_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP_DIR="${RPG_MENUBAR_BUILD_DIR:-$MENU_ROOT/build}/RuntimeProcessMenuBar.app"
PYTHON=""
if [ "${1:-}" = --python ]; then
    PYTHON="${2:-}"
    case "$PYTHON" in /*) ;; *) echo "Pythonの絶対パスを指定してください" >&2; exit 2 ;; esac
    [ -x "$PYTHON" ] || { echo "指定したPythonを実行できません" >&2; exit 2; }
    PYTHONPATH="$MENU_ROOT/../../src" "$PYTHON" -B -m runtime_process_guard.resident --help >/dev/null
    shift 2
fi
mkdir -p "$APP_DIR/Contents/MacOS"
xcrun swiftc -O -swift-version 5 -framework AppKit "$MENU_ROOT/main.swift" -o "$APP_DIR/Contents/MacOS/RuntimeProcessMenuBar"
cp "$MENU_ROOT/Info.plist" "$APP_DIR/Contents/Info.plist"
mkdir -p "$APP_DIR/Contents/Resources"
if [ -n "$PYTHON" ]; then
    "$PYTHON" - "$APP_DIR/Contents" "$MENU_ROOT/../../src/runtime_process_guard" <<'PYTHON_SCRIPT'
from pathlib import Path
import shlex
import sys

contents, source = map(Path, sys.argv[1:])
package = contents / "Resources" / "runtime_process_guard"
package.mkdir(exist_ok=True)
for path in source.glob("*.py"):
    (package / path.name).write_bytes(path.read_bytes())

launcher = contents / "MacOS" / "resident-collector"
launcher.write_text(
    '#!/bin/sh\nset -eu\n'
    '[ "${1:-}" = resident ] || exit 2\nshift\n'
    'resources=$(CDPATH= cd -- "$(dirname -- "$0")/../Resources" && pwd)\n'
    f'exec {shlex.quote(sys.executable)} -IB -c '
    + shlex.quote(
        "import sys; sys.path.insert(0, sys.argv.pop(1)); "
        "from runtime_process_guard.resident import main; raise SystemExit(main())"
    )
    + ' "$resources" "$@"\n'
)
launcher.chmod(0o755)
PYTHON_SCRIPT
else
    rm -f "$APP_DIR/Contents/MacOS/resident-collector"
fi
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
            --scope) CONFIG_KEY=scope ;;
            *) echo "未対応の引数です" >&2; exit 2 ;;
        esac
        if [ "$CONFIG_KEY" = scope ]; then
            [ "$2" = system ] || { echo "scopeはsystemのみ指定できます" >&2; exit 2; }
        else
            case "$2" in /*) ;; *) echo "絶対pathを指定してください" >&2; exit 2 ;; esac
        fi
        /usr/bin/plutil -insert "$CONFIG_KEY" -string "$2" "$DEFAULTS"
        shift 2
    done
fi
printf '%s\n' "$APP_DIR"

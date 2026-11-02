#!/bin/sh
# 実プロセスを観測しない静的fixture。配布アプリのCLIに指定しない。
FIXTURE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cat "$FIXTURE_ROOT/valid.json"

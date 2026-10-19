#!/bin/bash
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
trap 'echo; echo "Нажмите Enter, чтобы закрыть окно."; read -r _close' EXIT
python3 setup_local.py

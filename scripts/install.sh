#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v ffmpeg >/dev/null || { echo 'Install FFmpeg first; see README.md.' >&2; exit 1; }
command -v ffprobe >/dev/null || { echo 'ffprobe is required.' >&2; exit 1; }
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements-dev.txt

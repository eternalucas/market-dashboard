#!/bin/bash
# Double-click in Finder to start the dashboard.
# First run: creates a private Python environment and installs packages (a few minutes).
# Later runs: starts in a few seconds and opens in your browser.

cd "$(dirname "$0")" || exit 1

need_python() {
  osascript -e 'display dialog "The dashboard needs Python 3.10 or newer.\n\nInstall it from python.org (Downloads > macOS), then double-click Start Dashboard again." buttons {"Open python.org", "OK"} default button 1' \
    -e 'if button returned of result is "Open python.org" then open location "https://www.python.org/downloads/macos/"' >/dev/null 2>&1
  exit 1
}

PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
[ -z "$PY" ] && need_python

if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up (this takes a few minutes)..."
  "$PY" -m venv .venv || need_python
fi

echo "Checking packages..."
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt || { echo "Package install failed. Screenshot this window for Claude."; read -r -p "Press Enter to close."; exit 1; }

echo "Starting the dashboard at http://localhost:8501 ..."
(sleep 4 && open "http://localhost:8501") &
.venv/bin/python -m streamlit run app.py --server.headless true --browser.gatherUsageStats false

#!/bin/bash
# Double-click in Finder to record order books, options, futures and mempool
# data around the clock (the dashboard records too, but only while it's open).
# Leave this window open. It also keeps the Mac from going to sleep while it runs.
# Close the window to stop recording.

cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  osascript -e 'display dialog "Double-click Start Dashboard once first: it sets everything up." buttons {"OK"}' >/dev/null 2>&1
  exit 1
fi
.venv/bin/python -m pip install --quiet -r requirements.txt
echo "Recorder running $(date). Leave this window open; close it to stop."
exec caffeinate -i .venv/bin/python -m src.recorder

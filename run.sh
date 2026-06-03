#!/usr/bin/env bash
# Double-click: process any capture files in input/ and upload to WDGoWars.
# CLI:         forward any args to muninn.py through the venv interpreter.
#
# Examples:
#   ./run.sh                              # default: --upload everything in input/
#   ./run.sh /path/to/capture --upload    # one-off file
#   ./run.sh --setup                      # save API key + optional schedule
#   ./run.sh --schedule                   # configure scheduled task
#   ./run.sh --whoami                     # validate stored key
#   ./run.sh --watch /run/dump1090-fa --watch-glob aircraft.json --upload
#
# After the v2.0.8 PEP 668 fix, deps live in .venv/. This wrapper picks
# .venv/bin/python automatically so README examples stay short.

cd "$(dirname "$0")"
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY="python3"
fi
if [ $# -eq 0 ]; then
    "$PY" muninn.py --upload
else
    "$PY" muninn.py "$@"
fi
echo
if [ -t 0 ]; then
    read -n 1 -s -r -p "Press any key to close..."
    echo
fi

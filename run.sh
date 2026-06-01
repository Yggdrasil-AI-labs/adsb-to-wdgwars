#!/usr/bin/env bash
# Double-click (or run) to process any capture files in input/ and upload to WDGoWars.
cd "$(dirname "$0")"
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
else
    PY="python3"
fi
"$PY" muninn.py --upload
echo
read -n 1 -s -r -p "Press any key to close..."
echo

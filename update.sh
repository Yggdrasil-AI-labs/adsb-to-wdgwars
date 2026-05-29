#!/usr/bin/env bash
# Double-click (or run) this file to update Muninn to the latest version.
set -e
cd "$(dirname "$0")"
python3 muninn.py --update
echo
echo "Refreshing dependencies from requirements.txt..."
python3 -m pip install --upgrade -r requirements.txt
echo
read -n 1 -s -r -p "Press any key to close..."
echo

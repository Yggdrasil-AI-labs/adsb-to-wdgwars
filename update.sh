#!/usr/bin/env bash
# Double-click (or run) this file to update Muninn to the latest version.
cd "$(dirname "$0")"
python3 muninn.py --update
echo
read -n 1 -s -r -p "Press any key to close..."
echo

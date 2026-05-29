#!/usr/bin/env bash
# Double-click (or run) this once to install dependencies and save your WDGoWars API key.
set -e
cd "$(dirname "$0")"
echo "Installing dependencies from requirements.txt..."
python3 -m pip install --upgrade -r requirements.txt
echo
python3 muninn.py --setup
echo
read -n 1 -s -r -p "Press any key to close..."
echo

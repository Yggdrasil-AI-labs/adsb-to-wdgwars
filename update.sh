#!/usr/bin/env bash
# Double-click (or run) this file to update Muninn (refreshes deps + script).
# Refresh order matters: requirements.txt may have grown a new dep since
# the local copy was downloaded. Pull it first, install deps, THEN invoke
# muninn so it can import all of them cleanly.

set -e
cd "$(dirname "$0")"

echo "[1/3] Refreshing requirements.txt from GitHub..."
python3 -c "import urllib.request as u; u.urlretrieve('https://raw.githubusercontent.com/HiroAlleyCat/adsb-to-wdgwars/main/requirements.txt', 'requirements.txt')"

echo
echo "[2/3] Installing/refreshing dependencies..."
python3 -m pip install --upgrade -r requirements.txt

echo
echo "[3/3] Updating muninn.py..."
python3 muninn.py --update

echo
read -n 1 -s -r -p "Press any key to close..."
echo

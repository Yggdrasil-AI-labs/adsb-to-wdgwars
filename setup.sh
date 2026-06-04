#!/usr/bin/env bash
# Double-click (or run) this once to install dependencies and save your WDGoWars API key.
# We refresh requirements.txt from GitHub first in case the local copy
# in this clone/ZIP is stale relative to muninn.py's current dep list.
#
# Installs into a project-local .venv/ so this works on PEP 668 distros
# (Raspberry Pi OS Bookworm, Debian 12+, Ubuntu 23.04+, Homebrew Python)
# without --break-system-packages or polluting the system Python.

set -e
cd "$(dirname "$0")"

if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
    echo "Muninn requires Python 3.10 or newer. Your current python3 is:"
    python3 --version 2>/dev/null || echo "  (not found on PATH)"
    echo
    echo "Install Python 3.10+ from your package manager or https://python.org/downloads/ and re-run."
    exit 1
fi

VENV_DIR=".venv"

# Detect a venv that exists but has no pip inside it. This happens when an
# earlier `python3 -m venv` half-failed (interrupted setup, or a Python
# install with broken ensurepip such as Apple's /usr/bin/python3 stub).
# Without this check, reusing the broken venv silently loops on
# "No module named pip" every time setup.sh is re-run.
venv_has_pip() {
    [ -x "$VENV_DIR/bin/python" ] && \
        "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1
}

create_venv() {
    python3 -m venv "$VENV_DIR" 2>/dev/null
}

if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "[1/4] Creating virtual environment in $VENV_DIR/..."
    if ! create_venv; then
        echo
        echo "Failed to create venv. On Debian/Ubuntu/Raspberry Pi OS install the venv module:"
        echo "  sudo apt install -y python3-venv python3-full"
        echo "Then re-run ./setup.sh."
        exit 1
    fi
elif ! venv_has_pip; then
    echo "[1/4] Existing $VENV_DIR/ is missing pip, repairing..."
    if ! "$VENV_DIR/bin/python" -m ensurepip --upgrade >/dev/null 2>&1; then
        echo "        ensurepip not available, rebuilding venv from scratch..."
        rm -rf "$VENV_DIR"
        if ! create_venv; then
            echo
            echo "Failed to recreate venv. On Debian/Ubuntu/Raspberry Pi OS install:"
            echo "  sudo apt install -y python3-venv python3-full"
            exit 1
        fi
    fi
else
    echo "[1/4] Reusing existing $VENV_DIR/."
fi
VENV_PY="$VENV_DIR/bin/python"

# Final safety net: a freshly built venv can still lack pip when the host
# Python has no working ensurepip (most commonly Apple's stub python3 on
# macOS). Surface the real cause instead of failing 50 lines later.
if ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
    echo
    echo "Venv was created but has no pip. Your Python install's ensurepip is broken."
    echo "On macOS this usually means /usr/bin/python3 (Apple's stub). Install a real Python:"
    echo "  brew install python@3.12"
    echo "On Debian/Ubuntu/Raspberry Pi OS:"
    echo "  sudo apt install -y python3-venv python3-full"
    echo "Then 'rm -rf $VENV_DIR' and re-run ./setup.sh."
    exit 1
fi

echo
echo "[2/4] Refreshing requirements.txt from GitHub..."
"$VENV_PY" -c "import urllib.request as u; u.urlretrieve('https://raw.githubusercontent.com/HiroAlleyCat/adsb-to-wdgwars/main/requirements.txt', 'requirements.txt')"

echo
echo "[3/4] Installing dependencies..."
"$VENV_PY" -m pip install --upgrade pip >/dev/null
"$VENV_PY" -m pip install --upgrade -r requirements.txt

echo
echo "[4/4] Saving your WDGoWars API key..."
"$VENV_PY" muninn.py --setup

echo
if [ -t 0 ]; then
    read -n 1 -s -r -p "Press any key to close..."
    echo
fi

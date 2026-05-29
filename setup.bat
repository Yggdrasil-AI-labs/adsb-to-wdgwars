@echo off
REM Double-click this once to install dependencies and save your WDGoWars API key.
REM We refresh requirements.txt from GitHub first in case the local copy
REM in this ZIP is stale relative to muninn.py's current dep list.

echo [1/3] Refreshing requirements.txt from GitHub...
python -c "import urllib.request as u; u.urlretrieve('https://raw.githubusercontent.com/HiroAlleyCat/adsb-to-wdgwars/main/requirements.txt', r'%~dp0requirements.txt')"
if errorlevel 1 (
    echo.
    echo Could not fetch requirements.txt. Check internet connection and
    echo that Python is installed and on PATH.
    goto :done
)

echo.
echo [2/3] Installing dependencies...
python -m pip install --upgrade -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo pip install failed. See messages above. Common fixes:
    echo   - install git ^(needed for git+https deps^)
    echo   - run as administrator if pip needs elevated perms
    goto :done
)

echo.
echo [3/3] Saving your WDGoWars API key...
python "%~dp0muninn.py" --setup

:done
echo.
pause

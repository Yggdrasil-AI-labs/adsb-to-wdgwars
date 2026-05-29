@echo off
REM Double-click this file to update Muninn (refreshes deps + script).
REM Refresh order matters: requirements.txt may have grown a new dep
REM since the local copy was downloaded. Pull it first, install deps,
REM THEN invoke muninn so it can import all of them cleanly.

echo [1/3] Refreshing requirements.txt from GitHub...
python -c "import urllib.request as u; u.urlretrieve('https://raw.githubusercontent.com/HiroAlleyCat/adsb-to-wdgwars/main/requirements.txt', r'%~dp0requirements.txt')"
if errorlevel 1 (
    echo.
    echo Could not fetch requirements.txt. Check internet connection and
    echo that Python is installed and on PATH.
    goto :done
)

echo.
echo [2/3] Installing/refreshing dependencies...
python -m pip install --upgrade -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo pip install failed. See messages above. Common fixes:
    echo   - install git ^(needed for git+https deps^)
    echo   - run as administrator if pip needs elevated perms
    goto :done
)

echo.
echo [3/3] Updating muninn.py...
python "%~dp0muninn.py" --update

:done
echo.
pause

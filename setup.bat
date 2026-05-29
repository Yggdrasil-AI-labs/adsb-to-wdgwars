@echo off
REM Double-click this once to install dependencies and save your WDGoWars API key.
echo Installing dependencies from requirements.txt...
python -m pip install --upgrade -r "%~dp0requirements.txt"
if errorlevel 1 goto :done
echo.
python "%~dp0muninn.py" --setup
:done
echo.
pause

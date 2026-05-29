@echo off
REM Double-click this file to update Muninn to the latest version.
python "%~dp0muninn.py" --update
if errorlevel 1 goto :done
echo.
echo Refreshing dependencies from requirements.txt...
python -m pip install --upgrade -r "%~dp0requirements.txt"
:done
echo.
pause

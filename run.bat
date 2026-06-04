@echo off
REM Double-click: process any capture files in input/ and upload to WDGoWars.
REM CLI:         forward any args to muninn.py.
REM
REM Examples:
REM   run.bat                                       default: --upload everything in input/
REM   run.bat C:\path\capture.json --upload         one-off file
REM   run.bat --setup                               save API key + optional schedule
REM   run.bat --whoami                              validate stored key
REM   run.bat --watch C:\dump1090 --watch-glob aircraft.json --upload

if "%~1"=="" (
    python "%~dp0muninn.py" --upload
) else (
    python "%~dp0muninn.py" %*
)
echo.
pause

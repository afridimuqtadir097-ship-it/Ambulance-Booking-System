@echo off
title MedRush - Online Ambulance Booking
cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo.
    echo Virtual environment not found in this folder.
    echo Please set it up first by running these commands once:
    echo.
    echo     python -m venv venv
    echo     venv\Scripts\activate
    echo     pip install -r requirements.txt
    echo.
    pause
    exit /b
)

call venv\Scripts\activate.bat

echo.
echo Starting MedRush server...
echo (Leave this window open while using the app. Close it, or press Ctrl+C, to stop the server.)
echo.

REM Open the browser automatically a few seconds after the server starts
start "" cmd /c "timeout /t 3 >nul && start http://127.0.0.1:5000"

python app.py

echo.
echo Server stopped.
pause

@echo off
echo ============================================
echo  Text-to-SQL Generator - Environment Setup
echo ============================================
echo.

REM Create virtual environment
echo [1/4] Creating virtual environment...
python -m venv venv
echo Done.

REM Activate it
echo [2/4] Activating virtual environment...
call venv\Scripts\activate.bat

REM Upgrade pip
echo [3/4] Upgrading pip...
python -m pip install --upgrade pip

REM Install all requirements
echo [4/4] Installing packages...
pip install -r requirements.txt

echo.
echo ============================================
echo  Setup Complete!
echo  To run the app:
echo    1. venv\Scripts\activate
echo    2. python app.py
echo ============================================
pause

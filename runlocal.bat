@echo off
REM Local development launcher. Everything is resolved relative
REM to this file, so the project folder can be moved anywhere.
cd /d "%~dp0"
if not exist "%~dp0venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run fix-venv.bat on the drive root.
    pause
    exit /b 1
)
call "%~dp0venv\Scripts\activate.bat"
set DEBUG=True
set SECRET_KEY=dev-local-only-not-for-production
"%~dp0venv\Scripts\python.exe" manage.py runserver 8000
pause

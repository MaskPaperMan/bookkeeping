@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
    py -3.8-32 scripts\build_windows.py
) else (
    "%~1" scripts\build_windows.py
)
if errorlevel 1 (
    echo Build failed. See errors above.
    exit /b 1
)
echo Build complete. Test the EXE on target Windows systems before delivery.

@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

title Getfly CRM Log Parser

if not exist ".venv\Scripts\python.exe" (
    echo [LOI] Chua tim thay moi truong .venv.
    echo Hay chay INSTALL_AND_RUN.bat truoc.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" -c "import streamlit" >nul 2>nul
if errorlevel 1 (
    echo [LOI] Streamlit chua duoc cai trong .venv.
    echo Hay chay lai INSTALL_AND_RUN.bat.
    pause
    exit /b 1
)

echo Dang khoi dong Getfly CRM Log Parser...
echo Trinh duyet se mo tai http://localhost:8501
echo.

".venv\Scripts\python.exe" -m streamlit run app.py
if errorlevel 1 (
    echo.
    echo [LOI] Ung dung dung bat thuong.
    pause
    exit /b 1
)

endlocal

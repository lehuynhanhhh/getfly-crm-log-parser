@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

title Getfly CRM Log Parser - Installation

echo ==========================================
echo   GETFLY CRM LOG PARSER - INSTALLATION
echo ==========================================
echo.

set "PY_CMD="

REM Uu tien Python Launcher cua Windows.
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; print(sys.executable)" >nul 2>nul
    if not errorlevel 1 (
        set "PY_CMD=py -3"
    )
)

REM Thu lenh python, nhung phai chay duoc code that.
if not defined PY_CMD (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys; print(sys.executable)" >nul 2>nul
        if not errorlevel 1 (
            set "PY_CMD=python"
        )
    )
)

if not defined PY_CMD (
    echo [LOI] Khong tim thay Python that tren may.
    echo.
    echo Windows co the dang bat App Execution Alias tro den Microsoft Store,
    echo nhung Python chua duoc cai dat.
    echo.
    echo Vui long:
    echo   1. Cai Python 3.10 den 3.14.
    echo   2. Khi cai, chon "Add python.exe to PATH" neu co tuy chon nay.
    echo   3. Dong cua so CMD hien tai.
    echo   4. Mo lai thu muc va chay INSTALL_AND_RUN.bat.
    echo.
    echo Kiem tra sau khi cai bang lenh:
    echo   py -3 --version
    echo hoac:
    echo   python --version
    echo.
    pause
    exit /b 1
)

echo [OK] Da tim thay Python:
%PY_CMD% --version
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/4] Dang tao moi truong rieng .venv...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [LOI] Khong tao duoc moi truong .venv.
        pause
        exit /b 1
    )
) else (
    echo [1/4] Moi truong .venv da ton tai.
)

echo [2/4] Dang khoi tao pip...
".venv\Scripts\python.exe" -m ensurepip --upgrade
if errorlevel 1 (
    echo [LOI] Khong khoi tao duoc pip.
    pause
    exit /b 1
)

echo [3/4] Dang cap nhat pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo [LOI] Khong cap nhat duoc pip. Hay kiem tra ket noi Internet.
    pause
    exit /b 1
)

echo [4/4] Dang cai cac thu vien cua ung dung...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [LOI] Khong cai duoc thu vien. Hay kiem tra ket noi Internet va file requirements.txt.
    pause
    exit /b 1
)

echo.
echo [OK] Cai dat hoan tat.
echo Dang khoi dong Getfly CRM Log Parser...
echo Trinh duyet se mo tai http://localhost:8501
echo.

".venv\Scripts\python.exe" -m streamlit run app.py
if errorlevel 1 (
    echo.
    echo [LOI] Streamlit khong khoi dong duoc.
    pause
    exit /b 1
)

endlocal

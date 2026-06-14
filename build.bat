@echo off
setlocal enabledelayedexpansion
title MetadataImageSearch - Build

echo ============================================
echo  MetadataImageSearch - Build to EXE
echo ============================================
echo.

:: Install / upgrade PyInstaller
echo [1/3] Installing build dependencies...
venv\Scripts\python.exe -m pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
    echo ERROR: Failed to install build dependencies.
    pause
    exit /b 1
)
echo.

:: Clean previous build artefacts
echo [2/3] Cleaning previous build...
if exist "MetadataImageSearch.exe" del /f /q "MetadataImageSearch.exe"
if exist "src\dist\MetadataImageSearch.exe" del /f /q "src\dist\MetadataImageSearch.exe"
if exist "src\build" rmdir /s /q "src\build"
echo.

:: Build from inside src\ so PyInstaller resolves relative paths correctly
echo [3/3] Building executable...
pushd src
..\venv\Scripts\python.exe -m PyInstaller ^
    --onefile ^
    --windowed ^
    --icon=app_icon.ico ^
    --name=MetadataImageSearch ^
    --add-data "app_icon.ico;." ^
    --add-data "app_icon.png;." ^
    --add-data "app_icon_64.png;." ^
    --add-data "localization;localization" ^
    --hidden-import=PIL ^
    --hidden-import=PIL.Image ^
    --hidden-import=PIL.ImageTk ^
    --hidden-import=PIL.ImageDraw ^
    metadata_search.py
set BUILD_RESULT=%errorlevel%
popd

if %BUILD_RESULT% neq 0 (
    echo.
    echo ERROR: Build failed. Check output above for details.
    pause
    exit /b 1
)

:: Copy finished EXE to root
copy /y "src\dist\MetadataImageSearch.exe" "MetadataImageSearch.exe" > nul

echo.
echo ============================================
echo  Build successful!
echo  Output: MetadataImageSearch.exe
echo ============================================
echo.
pause

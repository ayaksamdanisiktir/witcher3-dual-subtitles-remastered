@echo off
setlocal EnableExtensions

cd /d "%~dp0"

echo.
echo [1/5] Checking Python...
where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found in PATH.
    echo Install Python 3.10+ and enable "Add python.exe to PATH".
    pause
    exit /b 1
)

echo [2/5] Checking pip...
python -m pip --version >nul 2>nul
if errorlevel 1 (
    echo pip is not available for this Python installation.
    pause
    exit /b 1
)

echo [3/5] Checking PyInstaller...
python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo PyInstaller is missing. Installing...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo Failed to install PyInstaller.
        pause
        exit /b 1
    )
) else (
    echo PyInstaller is already installed.
)

echo [4/5] Building EXE...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist Witcher3DualSubtitlesRemastered.spec del /q Witcher3DualSubtitlesRemastered.spec

python -m PyInstaller --noconfirm --clean --onefile --windowed --name "Witcher3DualSubtitlesRemastered" dual_subtitles_remastered.py
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

if not exist dist\Witcher3DualSubtitlesRemastered.exe (
    echo Build completed but EXE was not found in dist folder.
    pause
    exit /b 1
)

echo [5/5] Preparing release folder...
if exist release (
    rmdir /s /q release
    if exist release (
        echo Failed to clean release folder.
        echo Close running Witcher3DualSubtitlesRemastered.exe instances and retry.
        pause
        exit /b 1
    )
)

mkdir release
if errorlevel 1 (
    echo Failed to create release folder.
    pause
    exit /b 1
)

copy /y dist\Witcher3DualSubtitlesRemastered.exe release\ >nul
if errorlevel 1 (
    echo Failed to copy EXE into release folder.
    echo Close running Witcher3DualSubtitlesRemastered.exe instances and retry.
    pause
    exit /b 1
)

copy /y README.txt release\ >nul
if errorlevel 1 (
    echo Failed to copy README into release folder.
    pause
    exit /b 1
)

echo.
echo Build successful.
echo EXE path: %cd%\release\Witcher3DualSubtitlesRemastered.exe
echo.
echo You can now share the EXE in the release folder.
pause

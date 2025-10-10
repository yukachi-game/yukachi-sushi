@echo off
setlocal
pushd "%~dp0"

REM === Clean build dirs (optional) ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM === Create & activate venv ===
if not exist venv (
  python -m venv venv
)
call venv\Scripts\activate

REM === Install deps ===
python -m pip install --upgrade pip
pip install pyinstaller numpy opencv-python pygame

REM === Build (spec resolves paths relative to this .bat/.spec) ===
pyinstaller "sushi_game.spec"

echo.
echo ✅ Build finished. See ".\dist\yukachi_sushi_game.exe"
pause

popd
endlocal

@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
pushd "%~dp0"

echo ============================================
echo   Censr - сборка exe и установщика
echo ============================================
echo.

REM --- 1) Python ---
set "PY=python"
where py >nul 2>&1 && set "PY=py -3"
%PY% --version >nul 2>&1 || (echo [ОШИБКА] Python не найден в PATH. & goto :fail)
for /f "delims=" %%v in ('%PY% --version 2^>^&1') do echo Python: %%v

REM --- 2) Версия из censr/__init__.py ---
set "VER=1.5.0"
for /f "usebackq delims=" %%v in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$m=Select-String -Path 'censr\__init__.py' -Pattern '__version__\s*=\s*.(\d+\.\d+\.\d+)'; if($m){$m.Matches[0].Groups[1].Value}"`) do set "VER=%%v"
echo Версия: %VER%
echo.

REM --- 3) Зависимости + PyInstaller (в текущий Python) ---
echo [*] Устанавливаю зависимости...
%PY% -m pip install --upgrade pip >nul 2>&1
%PY% -m pip install -r requirements.txt pyinstaller || (echo [ОШИБКА] pip install не удался. & goto :fail)
echo.

REM --- 4) Чистка прошлой сборки ---
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM --- 5) PyInstaller: one-folder, без консоли ---
echo [*] Собираю exe (PyInstaller)...
%PY% -m PyInstaller --noconfirm --clean --windowed --noupx ^
  --name Censr --icon censr.ico ^
  --collect-all pymorphy3 ^
  --collect-all pymorphy3_dicts_ru ^
  --collect-all onnx_asr ^
  --collect-all onnxruntime ^
  --collect-all huggingface_hub ^
  --collect-all soxr ^
  --collect-all rapidfuzz ^
  --hidden-import PySide6.QtMultimedia ^
  --exclude-module tkinter ^
  --exclude-module pytest ^
  --exclude-module torch ^
  run_censr.py || (echo [ОШИБКА] PyInstaller не смог собрать. & goto :fail)

if not exist "dist\Censr\Censr.exe" (echo [ОШИБКА] dist\Censr\Censr.exe не создан. & goto :fail)
echo.

REM --- 6) Иконка/модель/шрифты рядом с exe (app_base_dir = папка exe) ---
echo [*] Кладу censr.ico и модель рядом с exe...
copy /y "censr.ico" "dist\Censr\" >nul
xcopy /e /i /y "models" "dist\Censr\models" >nul
if exist "fonts" xcopy /e /i /y "fonts" "dist\Censr\fonts" >nul

REM --- 7) ffmpeg/ffprobe -> dist\Censr\ffmpeg\ ---
echo [*] Готовлю ffmpeg...
set "FFDST=dist\Censr\ffmpeg"
set "FFOK=0"
if exist "ffmpeg\ffmpeg.exe" if exist "ffmpeg\ffprobe.exe" (
  if not exist "%FFDST%" mkdir "%FFDST%"
  copy /y "ffmpeg\ffmpeg.exe"  "%FFDST%\" >nul
  copy /y "ffmpeg\ffprobe.exe" "%FFDST%\" >nul
  set "FFOK=1"
  echo     ffmpeg взят из .\ffmpeg\
)
if "!FFOK!"=="0" (
  set "FFMPEG_SRC="
  set "FFPROBE_SRC="
  for %%E in (ffmpeg.exe)  do set "FFMPEG_SRC=%%~$PATH:E"
  for %%E in (ffprobe.exe) do set "FFPROBE_SRC=%%~$PATH:E"
)
if "!FFOK!"=="0" if defined FFMPEG_SRC if defined FFPROBE_SRC (
  if not exist "%FFDST%" mkdir "%FFDST%"
  copy /y "!FFMPEG_SRC!"  "%FFDST%\" >nul
  copy /y "!FFPROBE_SRC!" "%FFDST%\" >nul
  set "FFOK=1"
  echo     ffmpeg взят из PATH
)
if "!FFOK!"=="0" (
  echo [ВНИМАНИЕ] ffmpeg/ffprobe не найдены - ни в .\ffmpeg\, ни в PATH.
  echo            Сборка продолжится БЕЗ ffmpeg: тогда у пользователя он должен быть в PATH.
  echo            Положи ffmpeg.exe и ffprobe.exe в .\ffmpeg\ и пересобери, чтобы вшить их.
)
echo.

REM --- 7.5) Портативный zip ---
echo [*] Упаковываю портативный zip...
if not exist "installer\Output" mkdir "installer\Output"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\Censr\*' -DestinationPath 'installer\Output\Censr-portable-%VER%.zip' -Force" 2>nul && echo     installer\Output\Censr-portable-%VER%.zip || echo     [пропущено] zip не создан
echo.

REM --- 8) Установщик (Inno Setup) ---
echo [*] Ищу Inno Setup (ISCC.exe)...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC for /f "delims=" %%I in ('where iscc 2^>nul') do set "ISCC=%%I"

if not defined ISCC (
  echo [ВНИМАНИЕ] Inno Setup не найден. Установи: https://jrsoftware.org/isdl.php
  echo            Портативная сборка готова: dist\Censr\  и zip в installer\Output\.
  goto :portable_ok
)

echo [*] Собираю установщик (Inno Setup)...
"%ISCC%" /DMyAppVersion=%VER% installer.iss || (echo [ОШИБКА] ISCC не смог собрать установщик. & goto :fail)

echo.
echo ============================================
echo   ГОТОВО
echo   Портатив:    dist\Censr\Censr.exe
echo   Zip:         installer\Output\Censr-portable-%VER%.zip
echo   Установщик:  installer\Output\Censr-Setup-%VER%.exe
echo ============================================
goto :done

:portable_ok
echo.
echo ============================================
echo   ГОТОВО (только портатив)
echo   dist\Censr\Censr.exe
echo   installer\Output\Censr-portable-%VER%.zip
echo ============================================
goto :done

:fail
echo.
echo Сборка прервана.
popd ^& endlocal ^& exit /b 1

:done
popd ^& endlocal ^& exit /b 0

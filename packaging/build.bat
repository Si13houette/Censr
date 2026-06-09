@echo off
REM ==========================================================================
REM  Censr build script  (ASCII only - Cyrillic breaks cmd.exe parsing)
REM
REM  Steps: venv -> deps -> ffmpeg -> PyInstaller -> copy model+ffmpeg -> iss
REM  Run from anywhere:  packaging\build.bat
REM  Requirements: Python 3.10+ on PATH (py launcher), internet for ffmpeg.
REM ==========================================================================
setlocal enabledelayedexpansion
set "PKG=%~dp0"
set "ROOT=%PKG%.."
pushd "%ROOT%"

echo.
echo === [1/6] Python venv ===
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv || goto :fail
)
call ".venv\Scripts\activate.bat" || goto :fail

echo.
echo === [2/6] Dependencies ===
python -m pip install --upgrade pip || goto :fail
pip install -r requirements.txt || goto :fail
pip install pyinstaller || goto :fail

echo.
echo === [3/6] ffmpeg ===
set "FFDIR=%PKG%ffmpeg"
if not exist "%FFDIR%\ffmpeg.exe" (
    echo Downloading ffmpeg ^(essentials build^)...
    if not exist "%FFDIR%" mkdir "%FFDIR%"
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
      "$ErrorActionPreference='Stop'; $u='https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'; $z=Join-Path $env:TEMP 'censr_ffmpeg.zip'; Invoke-WebRequest -Uri $u -OutFile $z; $d=Join-Path $env:TEMP 'censr_ffmpeg_x'; if(Test-Path $d){Remove-Item $d -Recurse -Force}; Expand-Archive $z $d -Force; $f=Get-ChildItem $d -Recurse -Filter ffmpeg.exe | Select-Object -First 1; Copy-Item $f.FullName '%FFDIR%\ffmpeg.exe' -Force; Copy-Item (Join-Path $f.DirectoryName 'ffprobe.exe') '%FFDIR%\ffprobe.exe' -Force" || goto :fail
) else (
    echo ffmpeg already present, skipping download.
)

echo.
echo === [4/6] PyInstaller ===
pyinstaller --clean --noconfirm "%PKG%censr.spec" || goto :fail

echo.
echo === [5/6] Bundle model + ffmpeg next to exe ===
set "DIST=%ROOT%\dist\Censr"
if not exist "%DIST%\Censr.exe" (
    echo ERROR: build output missing: "%DIST%\Censr.exe"
    goto :fail
)
xcopy /E /I /Y "%ROOT%\models" "%DIST%\models" || goto :fail
if not exist "%DIST%\ffmpeg" mkdir "%DIST%\ffmpeg"
copy /Y "%FFDIR%\ffmpeg.exe"  "%DIST%\ffmpeg\" >nul || goto :fail
copy /Y "%FFDIR%\ffprobe.exe" "%DIST%\ffmpeg\" >nul || goto :fail
if exist "%PKG%dist-extra" xcopy /E /I /Y "%PKG%dist-extra" "%DIST%" >nul

echo.
echo === [6/6] Inno Setup installer (optional) ===
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
    "%ISCC%" "%PKG%censr.iss" || goto :fail
    echo Installer created in: "%ROOT%\dist\installer"
) else (
    echo Inno Setup not found - skipping installer.
    echo Install from https://jrsoftware.org/isdl.php then re-run, or
    echo zip the folder "%DIST%" and share it as a portable build.
)

echo.
echo === DONE ===
echo Portable build: "%DIST%"
popd
endlocal
exit /b 0

:fail
echo.
echo *** BUILD FAILED (see message above) ***
popd
endlocal
exit /b 1

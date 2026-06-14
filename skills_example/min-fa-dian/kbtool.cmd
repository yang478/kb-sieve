@echo off
setlocal enabledelayedexpansion
set "ROOT=%~dp0"
cd /d "%ROOT%"

set "OS_TAG=windows"
set "ARCH_TAG=%PROCESSOR_ARCHITECTURE%"
if /I "%ARCH_TAG%"=="AMD64" set "ARCH_TAG=x86_64"
if /I "%ARCH_TAG%"=="ARM64" set "ARCH_TAG=arm64"
if /I "%ARCH_TAG%"=="x86" set "ARCH_TAG=x86"
if "%ARCH_TAG%"=="" set "ARCH_TAG=unknown"

set "PLAT=%OS_TAG%-%ARCH_TAG%"
set "BIN=%ROOT%bin\%PLAT%\kbtool.exe"
set "BIN_SHA=%ROOT%bin\%PLAT%\kbtool.sha1"
set "ROOT_SHA=%ROOT%kbtool.sha1"
set "PY_SCRIPT=%ROOT%scripts\kbtool.py"

set "USEBIN=0"
if exist "%BIN%" if exist "%BIN_SHA%" if exist "%ROOT_SHA%" (
  for /f "usebackq delims=" %%a in ("%BIN_SHA%") do set "BINSHA=%%a"
  for /f "usebackq delims=" %%a in ("%ROOT_SHA%") do set "ROOTSHA=%%a"
  if /I "!BINSHA!"=="!ROOTSHA!" if not "!ROOTSHA!"=="" set "USEBIN=1"
)

if "%USEBIN%"=="1" (
  "%BIN%" %*
  exit /b %errorlevel%
)

if exist "%BIN%" if exist "%ROOT_SHA%" (
  if exist "%BIN_SHA%" (
    for /f "usebackq delims=" %%a in ("%BIN_SHA%") do set "BINSHA=%%a"
    for /f "usebackq delims=" %%a in ("%ROOT_SHA%") do set "ROOTSHA=%%a"
    if /I not "!BINSHA!"=="!ROOTSHA!" if not "!ROOTSHA!"=="" (
      echo [WARN] Found stale kbtool binary for %PLAT%. Falling back to python script. 1>&2
      echo [WARN] Rebuild with --package-kbtool on this platform to update bin\%PLAT%. 1>&2
    )
  )
)

where python3 >nul 2>nul
if "%errorlevel%"=="0" (
  python3 "%PY_SCRIPT%" %*
  exit /b %errorlevel%
)
where python >nul 2>nul
if "%errorlevel%"=="0" (
  python "%PY_SCRIPT%" %*
  exit /b %errorlevel%
)

if exist "%BIN%" (
  echo [WARN] Python not found; running kbtool binary for %PLAT% even if stale. 1>&2
  "%BIN%" %*
  exit /b %errorlevel%
)

echo [ERROR] No usable kbtool entry found. Missing scripts\kbtool.py and bin\%PLAT%\kbtool.exe. 1>&2
exit /b 2

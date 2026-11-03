@echo off
setlocal
title SPEDA - Start Local Testing
set "SPEDA_TEST_PYTHON=%USERPROFILE%\.codex\environments\speda-renovation\Scripts\python.exe"
if not exist "%SPEDA_TEST_PYTHON%" (
  echo The SPEDA test Python environment is missing.
  echo Expected: %SPEDA_TEST_PYTHON%
  pause
  exit /b 1
)
pushd "%~dp0"
if errorlevel 1 (
  echo Cannot open the SPEDA test project directory.
  pause
  exit /b 1
)
"%SPEDA_TEST_PYTHON%" "%~dp0scripts\local_testing.py" start
if errorlevel 1 (
  echo.
  echo Startup failed. Logs are in:
  echo %USERPROFILE%\.codex\local-testing\renovated-mark-vi
  popd
  pause
  exit /b 1
)
popd
start "" "http://127.0.0.1:5273/"
exit /b 0

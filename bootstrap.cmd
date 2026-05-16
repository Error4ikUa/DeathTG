@echo off
setlocal

if not "%TERMUX_VERSION%"=="" (
  echo DeathTG does not support Termux or Android terminal environments.
  exit /b 1
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 bootstrap.py
  exit /b %errorlevel%
)

where python >nul 2>nul
if %errorlevel%==0 (
  python bootstrap.py
  exit /b %errorlevel%
)

echo Python 3 is required.
exit /b 1

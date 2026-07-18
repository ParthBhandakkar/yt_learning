@echo off
REM ============================================================================
REM  liveTrade continuous runner for Windows (Exness / MT5).
REM  Usage:  run_forever.bat 97     (or 98)
REM  Keeps the engine alive: if it exits/crashes it waits 15s and restarts.
REM  Stop with Ctrl+C (twice) or by closing the window.
REM ============================================================================
setlocal
set STRAT=%1
if "%STRAT%"=="" set STRAT=97

REM ---- point this at your Python (the one where you pip-installed requirements) ----
set PYTHON=python
REM  e.g. set PYTHON=D:\Python\Python3_12_8\python.exe

cd /d "%~dp0"

:loop
echo [%date% %time%] starting liveTrade strategy %STRAT% ...
"%PYTHON%" run.py --strategy %STRAT%
echo [%date% %time%] engine exited (code %errorlevel%). Restarting in 15s...
timeout /t 15 /nobreak >nul
goto loop

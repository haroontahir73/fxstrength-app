@echo off
REM Currency Strength Desk - local scheduled refresh (Windows Task Scheduler).
REM Runs auto.py, which exits immediately unless a release printed, the daily
REM window elapsed, or a new COT report is due. Logs to data\refresh.log.
REM
REM PY: the Python 3.14 interpreter. Override for your machine if it is not on PATH,
REM e.g.  set "PY=C:\Path\to\pythonw.exe"
setlocal
if not defined PY set "PY=pythonw.exe"
set "DIR=%~dp0"
cd /d "%DIR%"
"%PY%" auto.py >> "%DIR%data\refresh.log" 2>&1

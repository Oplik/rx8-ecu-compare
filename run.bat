@echo off
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
python -m ecu_compare
if errorlevel 1 pause

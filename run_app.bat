@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0run_app.ps1" %*
IF ERRORLEVEL 1 pause

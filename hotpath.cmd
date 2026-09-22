@echo off
rem Hotpath in one command (Windows). Usage: hotpath.cmd <github-url | owner/repo | local-path> [options]
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0hotpath.ps1" %*
exit /b %ERRORLEVEL%

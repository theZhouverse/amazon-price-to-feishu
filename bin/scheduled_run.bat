@echo off
set "PROJECT_ROOT=%~dp0.."
wscript.exe //B //NoLogo "%PROJECT_ROOT%\bin\hidden_ps1.vbs" "%PROJECT_ROOT%\bin\scheduled_run.ps1" %*
exit /b %errorlevel%

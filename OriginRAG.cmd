@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "wsl=%SystemRoot%\System32\wsl.exe"
if exist "%SystemRoot%\Sysnative\wsl.exe" set "wsl=%SystemRoot%\Sysnative\wsl.exe"
if not exist "%wsl%" goto :needwsl
if defined ORIGINRAG_WSL_DISTRO goto :named
rem Prefer the user's default Ubuntu, including versioned distribution names.
"%wsl%" --exec grep -qx ID=ubuntu /etc/os-release >nul 2>&1
if errorlevel 1 goto :ubuntu
"%wsl%" --cd "%~dp0." --exec bash ./scripts/windows-workspace.sh
set "status=%errorlevel%"
goto :done
:ubuntu
set "ORIGINRAG_WSL_DISTRO=Ubuntu"
:named
"%wsl%" --distribution "%ORIGINRAG_WSL_DISTRO%" --exec /bin/true >nul 2>&1
if errorlevel 1 goto :needwsl
"%wsl%" --distribution "%ORIGINRAG_WSL_DISTRO%" --cd "%~dp0." --exec bash ./scripts/windows-workspace.sh
set "status=%errorlevel%"
:done
if not "%status%"=="0" pause
exit /b %status%
:needwsl
echo OriginRAG needs Ubuntu on WSL 2. In PowerShell as administrator, run:
echo   wsl --install -d Ubuntu
echo Restart if prompted, open Ubuntu once to create your user, then run this file again.
echo For an existing Ubuntu with another name, check: wsl -l -v
pause
exit /b 1

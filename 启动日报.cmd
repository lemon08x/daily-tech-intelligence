@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"

set "PROJECT_ROOT=%~dp0"
set "OPEN_SWITCH=-Open"
set "EXTRA_SWITCHES="

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--no-open" set "OPEN_SWITCH="
if /I "%~1"=="--offline" set "EXTRA_SWITCHES=%EXTRA_SWITCHES% -Offline"
if /I "%~1"=="--no-ai" set "EXTRA_SWITCHES=%EXTRA_SWITCHES% -NoAI"
if /I "%~1"=="--require-ai" set "EXTRA_SWITCHES=%EXTRA_SWITCHES% -RequireAI"
shift
goto parse_args

:args_done
pushd "%PROJECT_ROOT%" || goto :failed

if not exist ".venv\Scripts\python.exe" (
    echo First run: installing the isolated Python environment...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%scripts\setup.ps1"
    if errorlevel 1 goto :failed
)

echo Generating the unified technology intelligence and A-share digest...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%scripts\run_daily.ps1" %OPEN_SWITCH% %EXTRA_SWITCHES%
if errorlevel 1 goto :failed

popd
endlocal
exit /b 0

:failed
echo.
echo Startup failed. Please review the error above and logs\latest.log.
popd
pause
endlocal
exit /b 1

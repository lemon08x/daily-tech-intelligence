@echo off
chcp 65001 >nul
setlocal
set "PYTHONUTF8=1"

set "PROJECT_ROOT=%~dp0"
set "OPEN_SWITCH=-Open"
set "REQUIRE_SWITCH=-RequireAI"
set "EXTRA_SWITCHES="
set "NO_PAUSE="
set "PUSHED="
set "RUN_EXIT=1"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--no-open" set "OPEN_SWITCH="
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"
if /I "%~1"=="--scheduled" (
    set "EXTRA_SWITCHES=%EXTRA_SWITCHES% -Scheduled"
    set "NO_PAUSE=1"
)
if /I "%~1"=="--offline" set "EXTRA_SWITCHES=%EXTRA_SWITCHES% -Offline"
if /I "%~1"=="--no-ai" (
    set "REQUIRE_SWITCH="
    set "EXTRA_SWITCHES=%EXTRA_SWITCHES% -NoAI"
)
if /I "%~1"=="--require-ai" set "REQUIRE_SWITCH=-RequireAI"
if /I "%~1"=="--force-analysis" set "EXTRA_SWITCHES=%EXTRA_SWITCHES% -ForceAnalysis"
if /I "%~1"=="--experiment-id" (
    if "%~2"=="" goto bad_args
    set "EXTRA_SWITCHES=%EXTRA_SWITCHES% -ExperimentId ""%~2"""
    shift
)
shift
goto parse_args

:args_done
pushd "%PROJECT_ROOT%"
if errorlevel 1 goto failed
set "PUSHED=1"

if not exist ".venv\Scripts\python.exe" (
    echo First run: installing the isolated Python environment...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%scripts\setup.ps1"
    if errorlevel 1 goto failed
)

echo Generating daily digest with the shared daily configuration...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_ROOT%scripts\run_daily.ps1" %OPEN_SWITCH% %REQUIRE_SWITCH% %EXTRA_SWITCHES%
set "RUN_EXIT=%ERRORLEVEL%"
if not "%RUN_EXIT%"=="0" goto failed

popd
endlocal
exit /b 0

:bad_args
echo Missing value after --experiment-id.
goto failed

:failed
echo.
echo Startup failed. Please review the error above and logs\latest.log.
if defined PUSHED popd
if not defined NO_PAUSE pause
endlocal & exit /b %RUN_EXIT%

param(
    [switch]$Offline,
    [switch]$Open,
    [switch]$NoAI,
    [switch]$RequireAI,
    [string]$ExperimentId = "",
    [switch]$ForceAnalysis,
    [switch]$Scheduled,
    [string]$Config = ""
)

$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1: decode native UTF-8 and write UTF-8 logs.
$utf8Encoding = New-Object System.Text.UTF8Encoding $false
[Console]::InputEncoding = $utf8Encoding
[Console]::OutputEncoding = $utf8Encoding
$OutputEncoding = $utf8Encoding
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$env:PYTHONIOENCODING = 'utf-8:replace'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
if (-not $Config) {
    $Config = Join-Path $ProjectRoot "config\settings.daily-gpt.yaml"
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "尚未安装环境，请先运行 scripts\setup.ps1"
}
if ($NoAI -and $RequireAI) {
    throw "-NoAI 与 -RequireAI 不能同时使用"
}
if ($Scheduled) { $Open = $false }

# Resolve identity from the selected config so manual and scheduled runs cannot drift.
$configIdentity = & $Python -c 'import json,sys; from pathlib import Path; from daily_intel.core.settings import load_settings; c=load_settings(Path(sys.argv[1])); print(json.dumps(dict(experiment=c[''app''].get(''production_experiment_id'', ''default''), model=c[''llm''][''scout''][''model''])))' $Config
if ($LASTEXITCODE -ne 0) { throw "无法读取日报配置: $Config" }
$identity = $configIdentity | ConvertFrom-Json
if (-not $ExperimentId) { $ExperimentId = $identity.experiment }
$backend = $identity.model

if (Test-Path (Join-Path $ProjectRoot ".env")) {
    Get-Content (Join-Path $ProjectRoot ".env") -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -and $line -notlike "#*" -and $line -match "^([A-Za-z_][A-Za-z0-9_]*)=(.*)$") {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim().Trim('"').Trim("'"))
        }
    }
}
foreach ($key in @("OMLX_API_KEY", "DEEPSEEK_API_KEY", "QWEN_LAN_API_KEY", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "REPORT_TO")) {
    if (-not [Environment]::GetEnvironmentVariable($key)) {
        $fromUser = [Environment]::GetEnvironmentVariable($key, "User")
        if ($fromUser) {
            [Environment]::SetEnvironmentVariable($key, $fromUser)
        }
    }
}

$lanBypass = "localhost,127.0.0.1,::1,192.168.31.235,192.168.31.236"
if ($env:NO_PROXY) {
    $env:NO_PROXY = "$($env:NO_PROXY),$lanBypass"
} else {
    $env:NO_PROXY = $lanBypass
}
$env:no_proxy = $env:NO_PROXY
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss_fff"
$ReportDate = Get-Date -Format "yyyy-MM-dd"
$logPath = Join-Path $LogDir "latest.log"
if ($Scheduled) { $logPath = Join-Path $LogDir "agent_$Stamp.log" }
if ($RequireAI) { $mode = "require-ai" }
elseif ($NoAI) { $mode = "no-ai" }
elseif ($Offline) { $mode = "offline" }
else { $mode = "default" }

Write-Host ""
Write-Host "============================================================"
Write-Host " Daily digest"
Write-Host " Backend      $backend"
Write-Host " Config       $Config"
Write-Host " Experiment   $ExperimentId"
Write-Host " Mode         $mode"
if ($ForceAnalysis) {
    Write-Host " Analysis     force (ignore same-scope cache)"
}
Write-Host " Progress     [1/6] market -> collect -> git -> publish"
Write-Host " Log          $logPath"
Write-Host "============================================================"
Write-Host ""

$Arguments = @("-m", "daily_intel", "run", "--config", $Config)
if ($Offline) { $Arguments += "--offline" }
if ($Open) { $Arguments += "--open" }
if ($NoAI) { $Arguments += "--no-ai" }
if ($RequireAI) { $Arguments += "--require-ai" }
if ($ExperimentId) { $Arguments += @("--experiment-id", $ExperimentId) }
if ($ForceAnalysis) { $Arguments += "--force-analysis" }

# Native Python stderr (for example pypdf warnings) must not abort the run.
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}
$savedEa = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$code = 1
$utf8 = New-Object System.Text.UTF8Encoding $false
$writer = New-Object System.IO.StreamWriter $logPath, $false, $utf8
$writer.AutoFlush = $true
try {
    & $Python @Arguments 2>&1 | ForEach-Object {
        $line = if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { "$_" }
        $writer.WriteLine($line)
        Write-Host $line
    }
    if ($null -ne $LASTEXITCODE) { $code = $LASTEXITCODE } else { $code = 0 }
} finally {
    $writer.Flush()
    $writer.Dispose()
    $ErrorActionPreference = $savedEa
}
if ($Scheduled) {
    $SendLog = Join-Path $LogDir "send_$Stamp.log"
    & $Python (Join-Path $PSScriptRoot "send_report.py") --run-log $logPath --run-exit-code $code --report-date $ReportDate *>> $SendLog
    $SendExit = $LASTEXITCODE
    if ($null -eq $SendExit) { $SendExit = 1 }
    Get-Content $SendLog -ErrorAction SilentlyContinue
    if ($code -ne 0) { exit $code }
    exit $SendExit
}
if ($code -ne 0) { exit $code }

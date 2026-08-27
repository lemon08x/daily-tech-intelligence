param(
    [switch]$Offline,
    [switch]$Open,
    [switch]$NoAI,
    [switch]$RequireAI,
    [string]$ExperimentId = "deepseek-v4-flash",
    [switch]$ForceAnalysis,
    [string]$Config = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"
if (-not $Config) {
    $Config = Join-Path $ProjectRoot "config\settings.deepseek.yaml"
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "尚未安装环境，请先运行 scripts\setup.ps1"
}
if ($NoAI -and $RequireAI) {
    throw "-NoAI 与 -RequireAI 不能同时使用"
}

if (Test-Path (Join-Path $ProjectRoot ".env")) {
    Get-Content (Join-Path $ProjectRoot ".env") -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -and $line -notlike "#*" -and $line -match "^([A-Za-z_][A-Za-z0-9_]*)=(.*)$") {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim().Trim('"').Trim("'"))
        }
    }
}
foreach ($key in @("OMLX_API_KEY", "DEEPSEEK_API_KEY")) {
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
$env:PYTHONUNBUFFERED = "1"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Arguments = @("-m", "daily_intel", "run", "--config", $Config)
if ($Offline) { $Arguments += "--offline" }
if ($Open) { $Arguments += "--open" }
if ($NoAI) { $Arguments += "--no-ai" }
if ($RequireAI) { $Arguments += "--require-ai" }
if ($ExperimentId) { $Arguments += @("--experiment-id", $ExperimentId) }
if ($ForceAnalysis) { $Arguments += "--force-analysis" }

& $Python @Arguments *>&1 | Tee-Object -FilePath (Join-Path $LogDir "latest.log")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

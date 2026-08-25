param(
    [switch]$Offline,
    [switch]$Open,
    [switch]$NoAI,
    [switch]$RequireAI
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $ProjectRoot "logs"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "尚未安装环境，请先运行 scripts\setup.ps1"
}
if ($NoAI -and $RequireAI) {
    throw "-NoAI 与 -RequireAI 不能同时使用"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Arguments = @("-m", "daily_intel", "run", "--config", (Join-Path $ProjectRoot "config\settings.yaml"))
if ($Offline) { $Arguments += "--offline" }
if ($Open) { $Arguments += "--open" }
if ($NoAI) { $Arguments += "--no-ai" }
if ($RequireAI) { $Arguments += "--require-ai" }

& $Python @Arguments *>&1 | Tee-Object -FilePath (Join-Path $LogDir "latest.log")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

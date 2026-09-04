# 计划任务入口：DeepSeek V4 Flash 泛读、前十精读与邮件发送
# 由 Windows 任务计划程序每天 01:00 调用（含周末，见 install_agent_task.ps1）
$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (Test-Path (Join-Path $ProjectRoot ".env")) {
    Get-Content (Join-Path $ProjectRoot ".env") -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -and $line -notlike "#*" -and $line -match "^([A-Za-z_][A-Za-z0-9_]*)=(.*)$") {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim().Trim('"').Trim("'"))
        }
    }
}

foreach ($key in @("OMLX_API_KEY", "DEEPSEEK_API_KEY", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "REPORT_TO")) {
    if (-not [Environment]::GetEnvironmentVariable($key)) {
        $fromUser = [Environment]::GetEnvironmentVariable($key, "User")
        if ($fromUser) {
            [Environment]::SetEnvironmentVariable($key, $fromUser)
        }
    }
}

$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force $LogDir | Out-Null
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunLog = Join-Path $LogDir "agent_$Stamp.log"
$SendLog = Join-Path $LogDir "send_$Stamp.log"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Config = Join-Path $ProjectRoot "config\settings.deepseek.yaml"

Write-Host "[$Stamp] 启动 DeepSeek 泛读 + 前十精读"
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"
$lanBypass = "localhost,127.0.0.1,::1,192.168.31.236"
if ($env:NO_PROXY) {
    $env:NO_PROXY = "$($env:NO_PROXY),$lanBypass"
} else {
    $env:NO_PROXY = $lanBypass
}
$env:no_proxy = $env:NO_PROXY
& $Python -m daily_intel run --config $Config --require-ai --experiment-id deepseek-v4-flash *>> $RunLog
$RunExit = $LASTEXITCODE
if ($null -eq $RunExit) { $RunExit = 1 }
Write-Host "[$(Get-Date -Format HHmmss)] 日报生成退出码: $RunExit"

& $Python (Join-Path $PSScriptRoot "send_report.py") *>> $SendLog
$SendExit = $LASTEXITCODE
if ($null -eq $SendExit) { $SendExit = 1 }
Write-Host "[$(Get-Date -Format HHmmss)] 邮件发送退出码: $SendExit"
Get-Content $SendLog -ErrorAction SilentlyContinue

if ($RunExit -ne 0) { exit $RunExit }
exit $SendExit

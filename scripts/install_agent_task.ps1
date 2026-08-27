param(
    [string]$TaskName = "科技情报日报",
    [string]$At = "08:30"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot "run_daily_agent.ps1"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`"" -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 90)
$Description = "每天（含周末）用 DeepSeek V4 Flash 生成科技产业情报与A股观察日报并邮件发送"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Set-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings | Out-Null
    $task = Get-ScheduledTask -TaskName $TaskName
    $task.Description = $Description
    Set-ScheduledTask -InputObject $task | Out-Null
    Write-Host "已更新计划任务 '$TaskName'，每天 $At 运行（含周末，错过则下次登录时补跑，上限90分钟）。"
} else {
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description $Description | Out-Null
    Write-Host "已创建计划任务 '$TaskName'，每天 $At 运行（含周末，错过则下次登录时补跑，上限90分钟）。"
}

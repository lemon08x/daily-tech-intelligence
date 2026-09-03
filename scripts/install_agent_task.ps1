param(
    [string]$TaskName = "科技情报日报",
    [string]$At = "06:00"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot "run_daily_agent.ps1"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`"" -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 18)
$Description = "每天（含周末）用 DeepSeek V4 Flash 生成精读、泛读与Git情报日报并邮件发送"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Set-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings | Out-Null
    $task = Get-ScheduledTask -TaskName $TaskName
    $task.Description = $Description
    Set-ScheduledTask -InputObject $task | Out-Null
    Write-Host "已更新计划任务 '$TaskName'，每天 $At 运行（含周末，错过则下次登录时补跑，上限18小时，不并行重入）。"
} else {
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description $Description | Out-Null
    Write-Host "已创建计划任务 '$TaskName'，每天 $At 运行（含周末，错过则下次登录时补跑，上限18小时，不并行重入）。"
}

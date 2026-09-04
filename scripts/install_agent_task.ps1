param(
    [string]$TaskName = "科技情报日报",
    [string]$At = "01:00",
    [ValidateRange(1, 365)]
    [int]$DaysInterval = 1,
    [ValidateRange(1, 168)]
    [int]$ExecutionTimeLimitHours = 48
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Runner = Join-Path $PSScriptRoot "run_daily_agent.ps1"

$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`"" -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -DaysInterval $DaysInterval -At $At
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours $ExecutionTimeLimitHours)
$IntervalLabel = if ($DaysInterval -eq 1) { "每天" } else { "每 $DaysInterval 天" }
$Description = "${IntervalLabel}用 DeepSeek 泛读、复排前十精读并生成日报邮件"
$ScheduleSummary = "$IntervalLabel $At 运行（含周末，错过则下次登录时补跑，上限 $ExecutionTimeLimitHours 小时，不并行重入）"

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Set-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings | Out-Null
    $task = Get-ScheduledTask -TaskName $TaskName
    $task.Description = $Description
    Set-ScheduledTask -InputObject $task | Out-Null
    Write-Host "已更新计划任务 '$TaskName'，$ScheduleSummary。"
} else {
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description $Description | Out-Null
    Write-Host "已创建计划任务 '$TaskName'，$ScheduleSummary。"
}

# Compatibility entry for older task registrations. The root launcher owns startup.
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$Launcher = Join-Path $ProjectRoot "启动日报.cmd"
& $Launcher --scheduled
exit $LASTEXITCODE

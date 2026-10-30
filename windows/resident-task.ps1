[CmdletBinding()]
param(
    [ValidateSet('install','status','disable')][string]$Mode = 'status',
    [Parameter(Mandatory=$true)][string]$Pythonw,
    [Parameter(Mandatory=$true)][string]$StateDirectory
)
$ErrorActionPreference = 'Stop'
$name = 'Nexus Runtime Process Guard Resident'
$pythonPath = (Resolve-Path -LiteralPath $Pythonw).Path
if ([IO.Path]::GetFileName($pythonPath) -ine 'pythonw.exe') { throw 'pythonw.exe required' }
if (-not [IO.Path]::IsPathRooted($StateDirectory)) { throw 'Absolute state directory required' }
$statePath = [IO.Path]::GetFullPath($StateDirectory)
$report = Join-Path $statePath 'pressure-latest.json'
$stop = Join-Path $statePath 'stop-requested'
$arguments = '-I -m runtime_process_guard.pressure_watch --report-path "{0}" --stop-file "{1}" --interval-seconds 10' -f $report,$stop
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing) {
    $actions = @($existing.Actions)
    if ($actions.Count -ne 1 -or $actions[0].Execute -ine $pythonPath -or $actions[0].Arguments -cne $arguments) {
        throw 'Existing resident task does not match this installation; not overwritten'
    }
}
if ($Mode -eq 'install') {
    if (Test-Path -LiteralPath $stop) { throw 'Stop request exists; review before reinstall' }
    New-Item -ItemType Directory -Path $statePath -Force | Out-Null
    if (-not $existing) {
        $action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $statePath
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
        $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Low-cost pressure observation; managed launch guard remains separate' | Out-Null
    }
    # install is also the explicit resume operation, after stop-marker review.
    Enable-ScheduledTask -TaskName $name | Out-Null
    Start-ScheduledTask -TaskName $name
}
if ($Mode -eq 'disable' -and $existing) {
    Disable-ScheduledTask -TaskName $name | Out-Null
    New-Item -ItemType File -Path $stop -Force | Out-Null
}
$task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
[ordered]@{ task_name=$name; state=if($task){[string]$task.State}else{'missing'}; report_exists=(Test-Path -LiteralPath $report); read_only=$true } | ConvertTo-Json

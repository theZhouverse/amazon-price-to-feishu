param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('--install', '--remove')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runner = Join-Path $projectRoot 'bin\scheduled_run.ps1'
$hiddenLauncher = Join-Path $projectRoot 'bin\hidden_ps1.vbs'
$taskNames = @(
    'AmazonDaily_0730',
    'AmazonDaily_0730_weekday',
    'AmazonDaily_1530',
    'AmazonDaily_1530_weekday'
)

if ($Action -eq '--remove') {
    foreach ($taskName in $taskNames) {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
    }
    exit 0
}

# HTML service and firewall are managed separately; price install/remove must
# not start, stop, or grant network access for that optional service.
# Current production schedule: weekdays at 07:30 and 15:30, no expiration.
# Use the GUI-subsystem WScript launcher.  A task that calls a .bat/cmd wrapper
# can still flash a console before the inner PowerShell -WindowStyle Hidden is
# applied; wscript.exe avoids creating that console in the first place.
$settings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$afternoonWasDisabled = $false
$existingAfternoon = Get-ScheduledTask -TaskName 'AmazonDaily_1530' -ErrorAction SilentlyContinue
if ($existingAfternoon -and -not $existingAfternoon.Settings.Enabled) {
    $afternoonWasDisabled = $true
}
$definitions = @(
    [pscustomobject]@{ Name = 'AmazonDaily_0730'; Days = @('Monday'); Hour = 7; Minute = 30; Slot = 'monday_0730' },
    [pscustomobject]@{ Name = 'AmazonDaily_0730_weekday'; Days = @('Tuesday', 'Wednesday', 'Thursday', 'Friday'); Hour = 7; Minute = 30; Slot = 'weekday_0730' },
    [pscustomobject]@{ Name = 'AmazonDaily_1530'; Days = @('Monday'); Hour = 15; Minute = 30; Slot = 'monday_1530' },
    [pscustomobject]@{ Name = 'AmazonDaily_1530_weekday'; Days = @('Tuesday', 'Wednesday', 'Thursday', 'Friday'); Hour = 15; Minute = 30; Slot = 'weekday_1530' }
)
foreach ($item in $definitions) {
    $taskAction = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument (
        '//B //NoLogo "{0}" "{1}" "{2}"' -f $hiddenLauncher, $runner, $item.Slot)
    $trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $item.Days -At (
        Get-Date -Hour $item.Hour -Minute $item.Minute -Second 0)
    Register-ScheduledTask -TaskName $item.Name -Action $taskAction -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
}
if ($afternoonWasDisabled) {
    Disable-ScheduledTask -TaskName 'AmazonDaily_1530' | Out-Null
    Disable-ScheduledTask -TaskName 'AmazonDaily_1530_weekday' | Out-Null
}

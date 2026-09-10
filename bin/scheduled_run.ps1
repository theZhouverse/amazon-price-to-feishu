param(
    [ValidateSet('manual', 'monday_0730', 'monday_1530', 'weekday_0730', 'weekday_1530')]
    [string]$ScheduledSlot = 'manual'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$schedulerLogRoot = Join-Path $projectRoot 'outputs\scheduler_logs'
New-Item -ItemType Directory -Force -Path $schedulerLogRoot | Out-Null
$startedAt = Get-Date
$logPath = Join-Path $schedulerLogRoot ($startedAt.ToString('yyyy-MM-dd_HHmmss_fff') + "_$PID.log")
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    "[$($startedAt.ToString('o'))] ERROR: Python not found: $python" | Set-Content -LiteralPath $logPath -Encoding UTF8
    exit 2
}
Set-Location -LiteralPath $projectRoot
$env:PYTHONIOENCODING = 'utf-8'
# Python owns outputs/weekly_scheduler.lock for ALL CLI entrypoints.
# Do not acquire the same lock twice (parent PowerShell + child Python).
"[$($startedAt.ToString('o'))] START weekly-run --confirm --scheduled-slot $ScheduledSlot" | Set-Content -LiteralPath $logPath -Encoding UTF8
$exitCode = 1
$runnerError = $null
try {
    # A native Python traceback is written to stderr.  With
    # ErrorActionPreference=Stop, merging that stream into Out-File can abort
    # this wrapper before it records the final END line.  Keep the wrapper
    # diagnostic path non-terminating and preserve Python's real exit code.
    $ErrorActionPreference = 'Continue'
    & $python 'app\main.py' '--weekly-run' '--confirm' '--scheduled-slot' $ScheduledSlot 2>&1 | Out-File -LiteralPath $logPath -Encoding utf8 -Append
    if ($null -ne $LASTEXITCODE) {
        $exitCode = [int]$LASTEXITCODE
    }
} catch {
    $runnerError = $_
    $exitCode = 1
    "[$((Get-Date).ToString('o'))] ERROR scheduler wrapper: $($_.Exception.Message)" | Add-Content -LiteralPath $logPath -Encoding UTF8
} finally {
    $finishedAt = Get-Date
    if ($exitCode -eq 75) {
        "[$($finishedAt.ToString('o'))] SKIPPED overlapping/catch-up task; active run owns the global lock" | Add-Content -LiteralPath $logPath -Encoding UTF8
        $exitCode = 0
    }
    "[$($finishedAt.ToString('o'))] END exit=$exitCode elapsed_seconds=$([math]::Round(($finishedAt - $startedAt).TotalSeconds, 3))" | Add-Content -LiteralPath $logPath -Encoding UTF8
}
exit $exitCode

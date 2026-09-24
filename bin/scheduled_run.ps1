param(
    [ValidateSet('manual', 'monday_0730', 'monday_1530', 'weekday_0730', 'weekday_1530')]
    [string]$ScheduledSlot = 'manual'
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$globalCredentialLoader = 'D:\projects\lykj-projects-map\scripts\project-credential-env.ps1'
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
# Price/frontend runs never open Feedback or紫鸟店铺, but the price result
# still needs the shared Feishu app credentials for source/result I/O.  Load
# only that shared project context here; Feedback remains disabled below and
# has its own manual credential gate.
$env:AMAZON_FEEDBACK_ENABLED = 'false'
$credentialImportError = $null
$feishuCredentialsReady = (-not [string]::IsNullOrWhiteSpace($env:FS_APP_ID)) -and
    (-not [string]::IsNullOrWhiteSpace($env:FS_APP_SECRET))
if (-not $feishuCredentialsReady) {
    try {
        if (-not (Test-Path -LiteralPath $globalCredentialLoader -PathType Leaf)) {
            throw "PRICE_FEISHU_CREDENTIAL_LOADER_MISSING:$globalCredentialLoader"
        }
        . $globalCredentialLoader -ProjectId 'amazon_daily' -ProjectRoot $projectRoot -Import
    } catch {
        $credentialImportError = $_.Exception.Message
    }
    $feishuCredentialsReady = (-not [string]::IsNullOrWhiteSpace($env:FS_APP_ID)) -and
        (-not [string]::IsNullOrWhiteSpace($env:FS_APP_SECRET))
}
if ($null -ne $credentialImportError -or -not $feishuCredentialsReady) {
    $detail = if ($null -ne $credentialImportError) { $credentialImportError } else { 'PRICE_FEISHU_CREDENTIALS_MISSING_AFTER_IMPORT' }
    "[$($startedAt.ToString('o'))] BLOCKED: price run requires shared Feishu credentials: $detail" | Set-Content -LiteralPath $logPath -Encoding UTF8
    exit 12
}
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
# Python owns outputs/weekly_scheduler.lock for ALL CLI entrypoints.
# Do not acquire the same lock twice (parent PowerShell + child Python).
# The scheduled path is deliberately price/frontend-only. Feedback/Ziniao
# credentials belong to the separate manual `--feedback-only` entrypoint.
"[$($startedAt.ToString('o'))] START weekly-run --price-only --confirm --scheduled-slot $ScheduledSlot (Feedback disabled; shared Feishu credentials loaded)" | Set-Content -LiteralPath $logPath -Encoding UTF8
$exitCode = 1
$runnerError = $null
try {
    # A native Python traceback is written to stderr.  With
    # ErrorActionPreference=Stop, merging that stream into Out-File can abort
    # this wrapper before it records the final END line.  Keep the wrapper
    # diagnostic path non-terminating and preserve Python's real exit code.
    $ErrorActionPreference = 'Continue'
    # Scheduled jobs are intentionally price/frontend-only. Feedback is a
    # separate manual command: app\main.py --feedback-only --confirm.
    & $python 'app\main.py' '--weekly-run' '--price-only' '--confirm' '--scheduled-slot' $ScheduledSlot 2>&1 | Out-File -LiteralPath $logPath -Encoding utf8 -Append
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

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$StateDir,
    [ValidateSet('Daily', 'Weekly')][string]$Profile = 'Daily',
    [ValidateSet('auto', 'everything', 'filesystem')][string]$Backend = 'auto',
    [string]$Python = 'python'
)

$ErrorActionPreference = 'Stop'
$cli = Join-Path $PSScriptRoot 'file_intelligence_cli.py'
$resolvedState = [System.IO.Path]::GetFullPath($StateDir)
$logDir = Join-Path $resolvedState 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$logPath = Join-Path $logDir ("scheduled-{0}-{1}.log" -f $Profile.ToLowerInvariant(), $stamp)
$arguments = @($cli, '--state-dir', $resolvedState, 'maintain', '--backend', $Backend)
if ($Profile -eq 'Weekly') {
    $arguments += @('--deep', '--snapshot-kind', 'weekly')
}
else {
    $arguments += @('--snapshot-kind', 'daily')
}

try {
    & $Python @arguments 2>&1 | Tee-Object -FilePath $logPath
    if ($LASTEXITCODE -ne 0) {
        throw "Maintenance returned exit code $LASTEXITCODE."
    }
    & $Python $cli '--state-dir' $resolvedState 'dashboard' 2>&1 | Add-Content -LiteralPath $logPath -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        throw "Dashboard refresh returned exit code $LASTEXITCODE."
    }
}
catch {
    $_ | Out-String | Add-Content -LiteralPath $logPath -Encoding utf8
    exit 1
}

exit 0

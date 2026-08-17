[CmdletBinding()]
param([string[]]$Denylist = @())

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$audit = @('scripts\privacy_audit.py', '--root', '.', '--report', 'FILE_INTELLIGENCE_PUBLIC_RELEASE_AUDIT.md')
foreach ($marker in $Denylist) {
    $audit += @('--denylist', $marker)
}
Push-Location $root
try {
    & python @audit
    if ($LASTEXITCODE -ne 0) { throw 'Privacy audit failed.' }
    & python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed.' }
    & python scripts\verify_fresh_install.py --source .
    if ($LASTEXITCODE -ne 0) { throw 'Fresh-install verification failed.' }
}
finally {
    Pop-Location
}

[CmdletBinding()]
param(
    [string]$SourcePath = (Split-Path -Parent $PSScriptRoot),
    [string]$DestinationRoot = (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex\skills'),
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$source = [IO.Path]::GetFullPath($SourcePath)
$destinationRootPath = [IO.Path]::GetFullPath($DestinationRoot)
$destination = Join-Path $destinationRootPath 'file-intelligence'
$configuredState = [Environment]::GetEnvironmentVariable('FILE_INTELLIGENCE_STATE_DIR')
$localAppData = [Environment]::GetFolderPath('LocalApplicationData')
$state = if ($configuredState) { [IO.Path]::GetFullPath($configuredState) } else { Join-Path $localAppData 'FileIntelligence' }

if (-not (Test-Path -LiteralPath (Join-Path $source 'SKILL.md') -PathType Leaf)) {
    throw "SourcePath is not a File Intelligence Skill: $source"
}
if ((Split-Path -Leaf $destination) -ne 'file-intelligence') {
    throw 'Refusing an unexpected Skill destination.'
}
$destinationPrefix = $destination.TrimEnd('\') + '\'
$statePrefix = $state.TrimEnd('\') + '\'
if ($statePrefix.StartsWith($destinationPrefix, [StringComparison]::OrdinalIgnoreCase) -or $destinationPrefix.StartsWith($statePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Skill code and local machine state must use separate directories.'
}
if ((Test-Path -LiteralPath $destination) -and -not $Force) {
    throw "Skill already exists at $destination. Run update.ps1 for a code-only update."
}

New-Item -ItemType Directory -Path $destinationRootPath -Force | Out-Null
$temporary = Join-Path $destinationRootPath ('.file-intelligence.install.' + [Guid]::NewGuid().ToString('N'))
$backup = Join-Path $destinationRootPath ('.file-intelligence.previous.' + [Guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path $temporary -Force | Out-Null
    Get-ChildItem -LiteralPath $source -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $temporary -Recurse -Force
    }
    Get-ChildItem -LiteralPath $temporary -Directory -Recurse -Force |
        Where-Object { $_.Name -in @('__pycache__', '.pytest_cache', '.git') } |
        Sort-Object FullName -Descending |
        Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $temporary -File -Recurse -Force |
        Where-Object { $_.Extension -eq '.pyc' } |
        Remove-Item -Force
    if (Test-Path -LiteralPath $destination) {
        Move-Item -LiteralPath $destination -Destination $backup
    }
    Move-Item -LiteralPath $temporary -Destination $destination
    if (Test-Path -LiteralPath $backup) {
        Remove-Item -LiteralPath $backup -Recurse -Force
    }
}
catch {
    if ((Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $destination)) {
        Move-Item -LiteralPath $backup -Destination $destination
    }
    throw
}
finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Recurse -Force
    }
}

[ordered]@{
    status = 'INSTALLED'
    skill_path = $destination
    state_path = $state
    state_preserved = $true
    system_software_installed = $false
} | ConvertTo-Json

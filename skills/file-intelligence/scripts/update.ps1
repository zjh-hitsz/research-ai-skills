[CmdletBinding()]
param(
    [string]$SourcePath = (Split-Path -Parent $PSScriptRoot),
    [string]$DestinationRoot = (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.codex\skills')
)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'install.ps1') -SourcePath $SourcePath -DestinationRoot $DestinationRoot -Force

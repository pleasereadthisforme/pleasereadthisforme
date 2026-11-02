param(
    [Parameter(Mandatory=$true)][string]$RuntimeRoot,
    [ValidateSet('check', 'build')][string]$Action = 'check'
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$env:RUSTUP_HOME = Join-Path $RuntimeRoot 'rustup'
$env:CARGO_HOME = Join-Path $RuntimeRoot 'cargo'
$env:PATH = "$RuntimeRoot\cargo\bin;$env:PATH"
& "$RuntimeRoot\cargo\bin\cargo.exe" $Action --locked --manifest-path "$repo\desktop\src-tauri\Cargo.toml" --target-dir "$RuntimeRoot\desktop-target"
exit $LASTEXITCODE

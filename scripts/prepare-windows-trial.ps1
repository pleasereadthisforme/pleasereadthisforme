# Development trial, not a signed installer. Copies only explicitly listed sources.
param([Parameter(Mandatory=$true)][string]$RuntimeRoot)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$trial = Join-Path $RuntimeRoot 'staging'
$project = Join-Path $trial 'sidecar'
# Replace only the owned source staging tree; Copy-Item alone retains deleted modules.
$source = [IO.Path]::GetFullPath((Join-Path $repo 'sidecar\src')).TrimEnd('\')
$destination = [IO.Path]::GetFullPath((Join-Path $project 'src')).TrimEnd('\')
if ($source.Equals($destination, [StringComparison]::OrdinalIgnoreCase) -or
    $source.StartsWith($destination + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $destination.StartsWith($source + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Overlapping source staging' }
foreach ($path in @($source, $project, $destination)) {
    $current = $path
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            if ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked staging path' }
        }
        $current = Split-Path -Parent $current
    }
}
foreach ($path in @($source, $destination)) {
    if (Test-Path -LiteralPath $path) {
        if (Get-ChildItem -LiteralPath $path -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) { throw 'Linked source entry' }
    }
}
New-Item -ItemType Directory -Force $project | Out-Null
foreach ($name in @('pyproject.toml', 'uv.lock')) {
    Copy-Item -LiteralPath "$repo\sidecar\$name" -Destination $project -Force
}
$fresh = Join-Path $project ('src-new-' + [Guid]::NewGuid().ToString('N'))
$old = Join-Path $project ('src-old-' + [Guid]::NewGuid().ToString('N'))
try {
    Copy-Item -LiteralPath $source -Destination $fresh -Recurse -Force
    if (Test-Path -LiteralPath $destination) { Move-Item -LiteralPath $destination -Destination $old }
    try { Move-Item -LiteralPath $fresh -Destination $destination }
    catch {
        if (Test-Path -LiteralPath $old) { Move-Item -LiteralPath $old -Destination $destination }
        throw
    }
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force }
} finally {
    if (Test-Path -LiteralPath $fresh) { Remove-Item -LiteralPath $fresh -Recurse -Force }
}
$env:UV_CACHE_DIR = Join-Path $RuntimeRoot 'uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $RuntimeRoot 'managed-python'
$env:OPENAI_API_KEY = ''
$uv = Join-Path $RuntimeRoot 'uv\uv.exe'
& $uv sync --project $project --locked --no-dev --no-editable --reinstall-package prtfm --python 3.12 --python-preference only-managed
exit $LASTEXITCODE

param([Parameter(Mandatory=$true)][string]$RuntimeRoot, [Parameter(Mandatory=$true)][string]$JdkArchive)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$jdkRoot = Join-Path $RuntimeRoot 'jdk'
if (-not (Test-Path $jdkRoot)) { Expand-Archive -LiteralPath $JdkArchive -DestinationPath $jdkRoot }
$jdks = @(Get-ChildItem $jdkRoot -Directory)
if ($jdks.Count -ne 1) { throw 'Expected one JDK directory' }
$env:PATH = "$($jdks[0].FullName)\bin;$env:PATH"
$runtime = Join-Path $RuntimeRoot 'staging\runtime'
New-Item -ItemType Directory -Force $runtime | Out-Null
Copy-Item -LiteralPath "$repo\runtime\build_runtime.py" -Destination $runtime -Force
& "$RuntimeRoot\staging\sidecar\.venv\Scripts\python.exe" "$runtime\build_runtime.py"
exit $LASTEXITCODE

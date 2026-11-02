# Unsigned local preview. Runtime input must be a refreshed, verified directory bundle.
param(
    [Parameter(Mandatory=$true)][string]$RuntimeRoot,
    [Parameter(Mandatory=$true)][string]$BundlePath,
    [ValidatePattern('^\d+\.\d+\.\d+$')][string]$PreviewVersion,
    [switch]$DirectoryOnly
)
$ErrorActionPreference='Stop'
$repo=Split-Path -Parent $PSScriptRoot
$bundle=(Resolve-Path -LiteralPath $BundlePath).Path
$cli=Join-Path $RuntimeRoot 'packager\cargo-tauri.exe'
if(-not (Test-Path $cli)){throw 'Install the verified Tauri CLI 2.12.4 in RuntimeRoot\packager first'}
foreach($required in @('python\python.exe','python\pythonw.exe','commands.json','runtime\manifest.json','bundle.json')){
    if(-not (Test-Path (Join-Path $bundle $required))){throw "Missing runtime file: $required"}
}
$stage=Join-Path $RuntimeRoot 'installer-src'
New-Item -ItemType Directory -Force "$stage\src-tauri" | Out-Null
foreach($file in @('Cargo.toml','Cargo.lock','build.rs','tauri.conf.json','tauri.installer.conf.json')){
    Copy-Item -LiteralPath "$repo\desktop\src-tauri\$file" -Destination "$stage\src-tauri" -Force
}
Copy-Item -LiteralPath "$repo\sidecar\src\prtfm\localization\native.json" -Destination "$stage\src-tauri\native-messages.json" -Force
foreach($folder in @('src','icons')){
    $dest=Join-Path "$stage\src-tauri" $folder
    if(Test-Path $dest){Remove-Item -LiteralPath $dest -Recurse -Force}
    Copy-Item -LiteralPath "$repo\desktop\src-tauri\$folder" -Destination "$stage\src-tauri" -Recurse
}
New-Item -ItemType Directory -Force "$stage\installer" | Out-Null
Copy-Item -LiteralPath "$repo\desktop\installer\installer.nsi" -Destination "$stage\installer\installer.nsi" -Force
# Exact legacy distribution paths only; never recursively delete a runtime or app store.
$obsolete=Get-Content "$repo/desktop/installer/obsolete-runtime-files.json" -Raw | ConvertFrom-Json
$cleanup=@('; Generated from obsolete-runtime-files.json')
foreach($relative in $obsolete.files){
    if($relative -notmatch '^(python|tools)/[A-Za-z0-9_./-]+$' -or $relative -match '(^|/)\.\.(/|$)'){throw 'Invalid obsolete runtime path'}
    $cleanup+= 'Delete "$INSTDIR\runtime-bundle\'+$relative.Replace('/','\')+'"'
}
$obsoleteDirs=@{}
foreach($relative in $obsolete.files){
    $parent=$relative.Substring(0,$relative.LastIndexOf('/'))
    while($parent.Contains('/')){
        $obsoleteDirs[$parent]=$true
        $parent=$parent.Substring(0,$parent.LastIndexOf('/'))
    }
}
foreach($relative in ($obsoleteDirs.Keys | Sort-Object Length -Descending)){
    # RMDir without /r removes only empty retired directories.
    $cleanup+= 'RMDir "$INSTDIR\runtime-bundle\'+$relative.Replace('/','\')+'"'
}
$templatePath="$stage/installer/installer.nsi"
$template=[IO.File]::ReadAllText($templatePath).Replace('; PRTFM_OBSOLETE_RUNTIME_FILES',($cleanup -join "`r`n"))
[IO.File]::WriteAllText($templatePath,$template,(New-Object Text.UTF8Encoding($false)))
if(Test-Path "$stage\ui"){Remove-Item -LiteralPath "$stage\ui" -Recurse -Force}
Copy-Item -LiteralPath "$repo\desktop\ui" -Destination $stage -Recurse
# Retain the source bundle; exclude the package JAR only after fingerprint checks.
$duplicateParser='python/Lib/site-packages/opendataloader_pdf/jar/opendataloader-pdf-cli.jar'
$parserManifest=Get-Content (Join-Path $bundle 'runtime/manifest.json') -Raw | ConvertFrom-Json
$parserHash=(Get-FileHash (Join-Path $bundle 'runtime/parser.jar') -Algorithm SHA256).Hash.ToLower()
if($parserHash -ne $parserManifest.jar_sha256){throw 'Canonical parser fingerprint mismatch'}
$duplicatePath=Join-Path $bundle $duplicateParser
if((Test-Path -LiteralPath $duplicatePath) -and
   (Get-FileHash -LiteralPath $duplicatePath -Algorithm SHA256).Hash.ToLower() -ne $parserHash){
    throw 'Duplicate parser differs; refusing to exclude it'
}
$toolPlanJson=& (Join-Path $bundle 'python/python.exe') -B "$PSScriptRoot/search-tools-plan.py" --root (Join-Path $bundle 'tools/git')
if($LASTEXITCODE -ne 0){throw 'Search tool dependency validation failed'}
$toolPlan=$toolPlanJson | ConvertFrom-Json
$excludedTools=@($toolPlan.exclude | ForEach-Object {'tools/git/'+$_})
$testPlanJson=& (Join-Path $bundle 'python/python.exe') -B "$PSScriptRoot/python-test-plan.py" --root $bundle
if($LASTEXITCODE -ne 0){throw 'Python test exclusion validation failed'}
$testPlan=$testPlanJson | ConvertFrom-Json
# An allowlist keeps trial reports, launchers, test fixtures and user databases out.
$resources=[ordered]@{}
foreach($folder in @('python','runtime','tools','tokenizer-data')){
    Get-ChildItem -LiteralPath (Join-Path $bundle $folder) -Recurse -File | ForEach-Object {
        $rel=$_.FullName.Substring($bundle.Length+1).Replace('\','/')
        if($rel -ne $duplicateParser -and $rel -notin $excludedTools -and $rel -notin $testPlan.exclude -and $rel -notmatch '(^|/)__pycache__/|\.(pyc|pyo)$'){
            $resources[$_.FullName.Replace('\','/')]='runtime-bundle/'+$rel
        }
    }
}
foreach($file in @('commands.json','bundle.json','uv.lock')){
    $resources[(Join-Path $bundle $file).Replace('\','/')]='runtime-bundle/'+$file
}
foreach($relative in $obsolete.files){
    if(('runtime-bundle/'+$relative) -in $resources.Values){
        throw "Retired file reintroduced; update cleanup manifest first: $relative"
    }
}
$overrides=@{bundle=@{resources=$resources}}
if($PreviewVersion){$overrides.version=$PreviewVersion}
$version=if($PreviewVersion){$PreviewVersion}else{(Get-Content "$stage\src-tauri\tauri.conf.json" -Raw | ConvertFrom-Json).version}
$config=$overrides | ConvertTo-Json -Depth 5
$configPath=Join-Path $stage 'runtime-resources.json'
[IO.File]::WriteAllText($configPath,$config,(New-Object Text.UTF8Encoding($false)))
$env:RUSTUP_HOME=Join-Path $RuntimeRoot 'rustup'
$env:CARGO_HOME=Join-Path $RuntimeRoot 'cargo'
$env:CARGO_TARGET_DIR=Join-Path $RuntimeRoot 'installer-target'
$env:PATH="$RuntimeRoot\cargo\bin;$env:PATH"
# Java CDS resources retain read-only attributes when Tauri copies them. Clear
# only generated build-output attributes so a subsequent build can replace them.
$generatedResources=Join-Path $env:CARGO_TARGET_DIR 'release\runtime-bundle'
if(Test-Path $generatedResources){
    Get-ChildItem -LiteralPath $generatedResources -Recurse -File | Where-Object IsReadOnly | ForEach-Object {$_.IsReadOnly=$false}
}
# Tauri may retain resources removed from the manifest in a reused build output.
$staleParser=Join-Path $generatedResources $duplicateParser
if(Test-Path -LiteralPath $staleParser){Remove-Item -LiteralPath $staleParser -Force}
# Remove stale binary resources even when the input bundle is already reduced.
$generatedGit=Join-Path $generatedResources 'tools/git'
if(Test-Path -LiteralPath $generatedGit){
    Get-ChildItem -LiteralPath $generatedGit -Recurse -File | Where-Object {
        $_.Extension.ToLower() -in @('.exe','.dll') -and
        $_.FullName.Substring($generatedGit.Length+1).Replace('\','/') -notin $toolPlan.keep
    } | ForEach-Object {Remove-Item -LiteralPath $_.FullName -Force}
}
# A previous full payload may leave excluded tests behind in the build output.
if(Test-Path -LiteralPath $generatedResources){
    $staleTestsJson=& (Join-Path $bundle 'python/python.exe') -B "$PSScriptRoot/python-test-plan.py" --root $generatedResources
    if($LASTEXITCODE -ne 0){throw 'Stale Python test validation failed'}
    foreach($relative in ($staleTestsJson | ConvertFrom-Json).exclude){
        Remove-Item -LiteralPath (Join-Path $generatedResources $relative) -Force
    }
}
Push-Location $stage
try {
    [string[]]$buildArgs=@('build','--ci','--no-sign')
    if($DirectoryOnly){$buildArgs+= '--no-bundle'}else{$buildArgs+= @('--bundles','nsis')}
    $buildArgs+= @('--config','src-tauri/tauri.installer.conf.json','--config',$configPath,'--','--locked')
    & $cli @buildArgs
    if($LASTEXITCODE -ne 0){throw "Installer build failed ($LASTEXITCODE)"}
} finally {Pop-Location}
if($DirectoryOnly){
    $exe=Join-Path $env:CARGO_TARGET_DIR 'release\prtfm-desktop.exe'
    if(-not(Test-Path $exe)){throw 'Preview directory executable missing'}
    @{executable=$exe;runtime=$generatedResources;installer_created=$false;release_ready=$false}|ConvertTo-Json
    exit 0
}
$installers=@(Get-ChildItem "$env:CARGO_TARGET_DIR\release\bundle\nsis" -Filter "*_${version}_x64-setup.exe")
if(-not $installers.Count){throw 'No installer generated'}
$installers | ForEach-Object {
    [PSCustomObject]@{path=$_.FullName;bytes=$_.Length;sha256=(Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower();signed=$false;release_ready=$false}
} | ConvertTo-Json | Set-Content -Encoding UTF8 "$RuntimeRoot\installer-$version-result.json"
Copy-Item "$RuntimeRoot\installer-$version-result.json" "$RuntimeRoot\installer-result.json" -Force
Get-Content "$RuntimeRoot\installer-$version-result.json"

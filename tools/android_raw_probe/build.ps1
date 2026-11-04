param(
    [Parameter(Mandatory=$true)][string]$SdkRoot,
    [Parameter(Mandatory=$true)][string]$JdkRoot,
    [string]$BuildToolsVersion='30.0.3',
    [string]$OutputDirectory=(Join-Path $PSScriptRoot 'build')
)
$ErrorActionPreference='Stop'
$SdkRoot=(Resolve-Path -LiteralPath $SdkRoot).Path
$JdkRoot=(Resolve-Path -LiteralPath $JdkRoot).Path
$bt=Join-Path $SdkRoot "build-tools/$BuildToolsVersion"
$androidJar=Join-Path $SdkRoot 'platforms/android-30/android.jar'
foreach($p in @($androidJar,(Join-Path $bt 'aapt2.exe'),(Join-Path $bt 'd8.bat'),(Join-Path $bt 'apksigner.bat'),(Join-Path $JdkRoot 'bin/javac.exe'))) { if(-not (Test-Path -LiteralPath $p)){throw "Missing build input: $p"} }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$OutputDirectory=(Resolve-Path -LiteralPath $OutputDirectory).Path
# Each invocation gets a new directory; never recursively clean an existing output.
$run=Join-Path $OutputDirectory ('build-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,6))
New-Item -ItemType Directory -Path $run,(Join-Path $run 'classes'),(Join-Path $run 'dex'),(Join-Path $run 'generated/org/udc/rawprobe') | Out-Null
$revision=(& git -C $PSScriptRoot rev-parse HEAD).Trim()
$dirty=(@(& git -C $PSScriptRoot status --porcelain -- .).Count -gt 0).ToString().ToLowerInvariant()
$sources=@(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'app/src/org/udc/rawprobe') -Filter '*.java' -File | Sort-Object Name)
$hasher=[System.Security.Cryptography.SHA256]::Create()
$records=@()
foreach($file in @((Get-Item -LiteralPath (Join-Path $PSScriptRoot 'app/AndroidManifest.xml')))+$sources){
    $digest=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    $records+=($file.Name+':'+$digest)
}
$hash=[Convert]::ToHexString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes(($records -join "`n")))).ToLowerInvariant()
$buildInfo=Join-Path $run 'generated/org/udc/rawprobe/BuildInfo.java'
"package org.udc.rawprobe; final class BuildInfo { static final String COMMIT=`"$revision`"; static final boolean DIRTY=$dirty; static final String SOURCE_SHA256=`"$hash`"; }" | Set-Content -LiteralPath $buildInfo -Encoding utf8
$env:JAVA_HOME=$JdkRoot
$env:PATH=(Join-Path $JdkRoot 'bin')+[IO.Path]::PathSeparator+$env:PATH
function Checked([string]$Program,[string[]]$Arguments) { & $Program @Arguments; if($LASTEXITCODE -ne 0){throw "Build command failed ($LASTEXITCODE): $Program"} }
Checked (Join-Path $JdkRoot 'bin/javac.exe') (@('-encoding','UTF-8','-source','8','-target','8','-classpath',$androidJar,'-d',(Join-Path $run 'classes'))+@($sources.FullName)+@($buildInfo))
$classJar=Join-Path $run 'classes.jar'
Checked (Join-Path $JdkRoot 'bin/jar.exe') @('cf',$classJar,'-C',(Join-Path $run 'classes'),'.')
Checked (Join-Path $JdkRoot 'bin/java.exe') @('-cp',(Join-Path $bt 'lib/d8.jar'),'com.android.tools.r8.D8','--lib',$androidJar,'--min-api','28','--output',(Join-Path $run 'dex'),$classJar)
$unsigned=Join-Path $run 'unsigned.apk'
Checked (Join-Path $bt 'aapt2.exe') @('link','-I',$androidJar,'--manifest',(Join-Path $PSScriptRoot 'app/AndroidManifest.xml'),'-o',$unsigned,'--min-sdk-version','28','--target-sdk-version','30')
Add-Type -AssemblyName System.IO.Compression
$zip=[IO.Compression.ZipFile]::Open($unsigned,[IO.Compression.ZipArchiveMode]::Update)
try{[IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip,(Join-Path $run 'dex/classes.dex'),'classes.dex') | Out-Null}finally{$zip.Dispose()}
$aligned=Join-Path $run 'aligned.apk'
Checked (Join-Path $bt 'zipalign.exe') @('-f','4',$unsigned,$aligned)
$key=Join-Path $OutputDirectory 'debug.keystore'
if(-not(Test-Path -LiteralPath $key)){ Checked (Join-Path $JdkRoot 'bin/keytool.exe') @('-genkeypair','-keystore',$key,'-storepass','android','-alias','androiddebugkey','-keypass','android','-keyalg','RSA','-keysize','2048','-validity','10000','-dname','CN=UDC Local Debug') }
$apk=Join-Path $run 'udc-raw-probe.apk'
Checked (Join-Path $JdkRoot 'bin/java.exe') @('-jar',(Join-Path $bt 'lib/apksigner.jar'),'sign','--ks',$key,'--ks-key-alias','androiddebugkey','--ks-pass','pass:android','--key-pass','pass:android','--out',$apk,$aligned)
Checked (Join-Path $JdkRoot 'bin/java.exe') @('-jar',(Join-Path $bt 'lib/apksigner.jar'),'verify','--verbose',$apk)
@{apk=$apk;sha256=(Get-FileHash -LiteralPath $apk -Algorithm SHA256).Hash.ToLowerInvariant();source_commit=$revision;source_dirty=($dirty -eq 'true');app_source_sha256=$hash;sdk_platform=30;build_tools=$BuildToolsVersion;javac=(& (Join-Path $JdkRoot 'bin/javac.exe') -version 2>&1 | Out-String).Trim();source_files=$records} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $run 'build_manifest.json') -Encoding utf8
Write-Output "APK: $apk"

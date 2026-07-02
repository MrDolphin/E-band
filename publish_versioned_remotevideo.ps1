param(
    [Parameter(Mandatory = $false)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]*$')]
    [string]$Label = 'build',

    [Parameter(Mandatory = $false)]
    [switch]$UpdateLauncher
)

$ErrorActionPreference = 'Stop'

function Get-RelativeDirectoryPath {
    param(
        [string]$BaseDirectory,
        [string]$TargetDirectory
    )

    $basePath = [IO.Path]::GetFullPath($BaseDirectory).TrimEnd('\') + '\'
    $targetPath = [IO.Path]::GetFullPath($TargetDirectory).TrimEnd('\') + '\'
    $relativeUri = ([Uri]$basePath).MakeRelativeUri([Uri]$targetPath)
    return [Uri]::UnescapeDataString($relativeUri.ToString()).Replace('/', '\').TrimEnd('\')
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Join-Path $repoRoot 'Material\videotool_decompiled\remotevideo_usepdb'
$project = Join-Path $projectRoot 'remotevideo.csproj'
$sourcePaths = @(
    'Material/videotool_decompiled/remotevideo_usepdb/remotevideo',
    'Material/videotool_decompiled/remotevideo_usepdb/Properties',
    'Material/videotool_decompiled/remotevideo_usepdb/remotevideo.csproj'
)

$sourceStatus = & git -C $repoRoot status --porcelain -- $sourcePaths
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to inspect Git source status.'
}
if ($sourceStatus) {
    throw "Application source has uncommitted changes. Commit first:`n$sourceStatus"
}

$commit = (& git -C $repoRoot rev-parse --short=7 HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commit -notmatch '^[0-9a-f]{7}$') {
    throw 'Unable to resolve a seven-character Git commit ID.'
}

$assemblyName = "remotevideo-$commit"
$releaseName = "video-modem-stage14-$Label-$commit"
$outputDirectory = Join-Path $projectRoot "bin\$releaseName\Release\net8.0-windows7.0"
$intermediateDirectory = Join-Path $repoRoot ".codex-build\versioned-$commit\obj\"

& dotnet build $project `
    -c Release `
    -t:Rebuild `
    "-p:BuildCommit=$commit" `
    "-p:AssemblyName=$assemblyName" `
    "-p:BaseIntermediateOutputPath=$intermediateDirectory" `
    "-p:OutputPath=$outputDirectory\" `
    '-p:DefaultItemExcludes=obj\**'
if ($LASTEXITCODE -ne 0) {
    throw 'Versioned remotevideo build failed.'
}

$executable = Join-Path $outputDirectory "$assemblyName.exe"
$metadataSource = Get-ChildItem `
    -LiteralPath $intermediateDirectory `
    -Filter 'BuildCommit.g.cs' `
    -Recurse | Select-Object -First 1
if (-not (Test-Path -LiteralPath $executable)) {
    throw "Expected executable was not produced: $executable"
}
if ($null -eq $metadataSource -or
    (Get-Content -Raw -LiteralPath $metadataSource.FullName) -notmatch [regex]::Escape($commit)) {
    throw 'BuildCommit metadata was not generated with the expected commit.'
}

if ($UpdateLauncher) {
    $relativeDirectory = Get-RelativeDirectoryPath $repoRoot $outputDirectory
    $launcher = @(
        '@echo off',
        "set `"APP_DIR=%~dp0$relativeDirectory`"",
        "start `"`" `"%APP_DIR%\$assemblyName.exe`""
    )
    Set-Content -LiteralPath (Join-Path $repoRoot 'run_remotevideo.cmd') `
        -Value $launcher `
        -Encoding Ascii
}

Write-Host "Commit: $commit"
Write-Host "Directory: $outputDirectory"
Write-Host "Executable: $executable"
Write-Host "Launcher updated: $($UpdateLauncher.IsPresent)"

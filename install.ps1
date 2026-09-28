# Bootstrap a fixed uv release, then hand installation to the published package.
# Usage: ./install.ps1 -Version 0.19.0 [-WheelUrl https://.../hey_my_buddy-0.19.0-py3-none-any.whl]
param([string]$Version = '0.19.0', [string]$WheelUrl = '')
$ErrorActionPreference = 'Stop'
$uvVersion = '0.12.19'
$sumsHash = '580e9742bc1ca4f9a6da3c4aa3db2dcceb0d0be84d75647abdae91bff68e52fc'
function Fail([string]$Code, [string]$Message, [string]$Repair) {
    [Console]::Error.WriteLine("${Code}: ${Message} Repair: ${Repair}")
    exit 1
}
if ($Version -notmatch '^[0-9A-Za-z][0-9A-Za-z.+_-]*$') { Fail 'BOOTSTRAP_ARGS' 'Invalid version.' 'Pass a fixed version such as 0.19.0.' }
if ($WheelUrl -and $WheelUrl -notmatch '^https://\S+\.whl$') { Fail 'BOOTSTRAP_ARGS' 'Invalid wheel URL.' 'Use an HTTPS .whl URL.' }
if ($WheelUrl -and $WheelUrl -notmatch "/hey_my_buddy-$([regex]::Escape($Version))-[^/]+\.whl$") {
    Fail 'BOOTSTRAP_ARGS' 'Wheel version or name does not match.' "Use a hey_my_buddy wheel for -Version $Version."
}
$dataHome = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME 'AppData\Local' }
$privateDir = Join-Path $dataHome "hey-my-buddy\uv\$uvVersion"
$uv = Join-Path $privateDir 'uv.exe'
$systemUv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
if ($systemUv) { $uv = $systemUv.Source }
elseif (-not (Test-Path $uv)) {
    $platform = switch ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()) {
        'X64' { 'x86_64-pc-windows-msvc' }
        'Arm64' { 'aarch64-pc-windows-msvc' }
        'X86' { 'i686-pc-windows-msvc' }
        default { Fail 'BOOTSTRAP_PLATFORM' 'Unsupported Windows architecture.' 'Install uv manually and rerun this script.' }
    }
    $archive = "uv-$platform.zip"
    $base = "https://releases.astral.sh/github/uv/releases/download/$uvVersion"
    $temp = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Path $temp | Out-Null
        try {
            Invoke-WebRequest "$base/sha256.sum" -OutFile (Join-Path $temp 'sha256.sum')
            Invoke-WebRequest "$base/$archive" -OutFile (Join-Path $temp $archive)
        } catch { Fail 'BOOTSTRAP_FETCH' 'Cannot download uv.' 'Check network access to releases.astral.sh and retry.' }
        if ((Get-FileHash (Join-Path $temp 'sha256.sum') -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sumsHash) {
            Fail 'BOOTSTRAP_VERIFY' 'uv checksum list does not match the pinned release.' 'Get a trusted copy of uv and retry.'
        }
        $line = Get-Content (Join-Path $temp 'sha256.sum') | Where-Object { $_ -match "^[0-9a-fA-F]{64}\s+\*?$([regex]::Escape($archive))$" } | Select-Object -First 1
        if (-not $line) { Fail 'BOOTSTRAP_VERIFY' "No checksum for $archive." 'Install uv manually and retry.' }
        $expected = ($line -split '\s+')[0]
        if ((Get-FileHash (Join-Path $temp $archive) -Algorithm SHA256).Hash -ine $expected) {
            Fail 'BOOTSTRAP_VERIFY' 'uv archive checksum mismatch.' 'Discard the download and retry.'
        }
        try {
            Expand-Archive (Join-Path $temp $archive) -DestinationPath $temp
            New-Item -ItemType Directory -Path $privateDir -Force | Out-Null
            Copy-Item (Join-Path $temp 'uv.exe') "$uv.new" -Force
            Move-Item "$uv.new" $uv -Force
        } catch { Fail 'BOOTSTRAP_EXTRACT' 'Cannot place private uv.' "Allow writes to $privateDir and retry." }
    } finally { Remove-Item $temp -Recurse -Force -ErrorAction SilentlyContinue }
}
$source = if ($WheelUrl) { $WheelUrl } else { "hey-my-buddy==$Version" }
Write-Output "Package source: $source"
Write-Output "uv: $uv"
Write-Output "Writes: $privateDir; ~/.agents/skills/buddy; ~/.claude/skills/buddy; ~/.local/share/hey-my-buddy"
$oldUvBin = $env:UV_BIN
try {
    $env:UV_BIN = $uv
    & $uv tool run --from $source hey-my-buddy install
    $installExit = $LASTEXITCODE
} finally {
    if ($null -eq $oldUvBin) { Remove-Item Env:UV_BIN -ErrorAction SilentlyContinue } else { $env:UV_BIN = $oldUvBin }
}
if ($installExit -ne 0) { Fail 'PACKAGE_INSTALL_FAILED' 'Package installation failed.' 'Read the installer error above, correct it, and rerun this command.' }

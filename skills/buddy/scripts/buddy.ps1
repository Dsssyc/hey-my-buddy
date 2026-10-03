$ErrorActionPreference = 'Stop'
trap {
    '{"contractVersion":"unknown","error":{"code":"LAUNCH_FAILED","message":"Run the fixed-version package install command outside the Host sandbox to repair this launcher"}}'
    exit 1
}
# Avoid the legacy native-command binder dropping embedded JSON quotes on
# Windows PowerShell 5.1. Newer runtimes expose ArgumentList directly.
function ConvertTo-BuddyNativeArgument([string] $Value) {
    return '"' + [regex]::Replace($Value, '(\\*)("|$)', {
        param($Match)
        $slashes = $Match.Groups[1].Value
        if ($Match.Groups[2].Value -eq '"') { return $slashes + $slashes + '\"' }
        return $slashes + $slashes
    }) + '"'
}
function Invoke-BuddyProcess([string] $Executable, [string[]] $Arguments) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.UseShellExecute = $false
    $info.EnvironmentVariables['PYTHONUTF8'] = '1'
    if ($null -ne $info.PSObject.Properties['ArgumentList']) {
        foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    } else {
        $info.Arguments = ($Arguments | ForEach-Object { ConvertTo-BuddyNativeArgument $_ }) -join ' '
    }
    $child = [System.Diagnostics.Process]::Start($info)
    try { $child.WaitForExit(); return $child.ExitCode } finally { $child.Dispose() }
}
$skill = Split-Path -Parent $PSScriptRoot
$project = Join-Path $skill 'package'
if (-not (Test-Path (Join-Path $project 'pyproject.toml'))) {
    $project = (Resolve-Path (Join-Path $skill '../..')).Path
}
$env:BUDDY_SKILL_DIR = $skill
$launcher = Join-Path $project 'src/hey_my_buddy/install/launcher.py'
if (($args.Count -gt 0 -and $args[0] -in @('install', 'upgrade')) -or $env:BUDDY_DEV_SOURCE -eq '1') {
    $uv = if ($env:UV_BIN) { $env:UV_BIN } else { 'uv' }
    $result = Invoke-BuddyProcess $uv (@('run', '--frozen', '--python', '3.12', '--project', $project, 'python', $launcher) + $args)
} else {
    $hint = Join-Path $skill '.runtime-python'
    $python = if ($env:BUDDY_BOOTSTRAP_PYTHON) { $env:BUDDY_BOOTSTRAP_PYTHON } elseif (Test-Path $hint) { (Get-Content -Raw $hint).Trim() } else { 'python' }
    $result = Invoke-BuddyProcess $python (@($launcher) + $args)
}
exit $result

$ErrorActionPreference = 'Stop'
trap {
    '{"contractVersion":"unknown","error":{"code":"LAUNCH_FAILED","message":"Run the fixed-version package install command outside the Host sandbox to repair this launcher"}}'
    exit 1
}
$skill = Split-Path -Parent $PSScriptRoot
$project = Join-Path $skill 'package'
if (-not (Test-Path (Join-Path $project 'pyproject.toml'))) {
    $project = (Resolve-Path (Join-Path $skill '../..')).Path
}
$env:BUDDY_SKILL_DIR = $skill
$launcher = Join-Path $project 'src/buddy/launcher.py'
if (($args.Count -gt 0 -and $args[0] -in @('install', 'upgrade')) -or $env:BUDDY_DEV_SOURCE -eq '1') {
    $uv = if ($env:UV_BIN) { $env:UV_BIN } else { 'uv' }
    & $uv run --frozen --python 3.12 --project $project python $launcher @args
} else {
    $hint = Join-Path $skill '.runtime-python'
    $python = if ($env:BUDDY_BOOTSTRAP_PYTHON) { $env:BUDDY_BOOTSTRAP_PYTHON } elseif (Test-Path $hint) { (Get-Content -Raw $hint).Trim() } else { 'python' }
    & $python $launcher @args
}
exit $LASTEXITCODE

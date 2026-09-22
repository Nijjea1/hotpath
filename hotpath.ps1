# Hotpath in one command (Windows PowerShell).
#   .\hotpath.ps1 https://github.com/you/your-repo        # or owner/repo, or a local path
#   .\hotpath.ps1 https://github.com/you/your-repo --yes  # accept every default, including pushing the PR branch
# Creates .venv next to this script on first use, installs Hotpath into it, then runs `hotpath go`.
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($args.Count -eq 0 -or $args[0] -in @("-h", "--help")) {
    Write-Host "usage: hotpath.cmd <github-url | owner/repo | git-url | local-path> [options]"
    Write-Host "       hotpath.cmd --help-go    (all options)"
    if ($args.Count -eq 0) { exit 2 } else { exit 0 }
}

# Resolve a local-path target before changing directory, so relative paths keep working.
$Forward = @($args)
if (Test-Path -LiteralPath $Forward[0]) { $Forward[0] = (Resolve-Path -LiteralPath $Forward[0]).Path }
if ($Forward[0] -eq "--help-go") { $Forward = @("--help") }

function Find-Python {
    $candidates = @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("py", "-3.14"), @("python"), @("python3"))
    foreach ($c in $candidates) {
        $exe = $c[0]; $rest = @($c | Select-Object -Skip 1)
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        try {
            $ok = & $exe @rest -c "import sys; print(sys.version_info >= (3, 11))" 2>$null
            if ($LASTEXITCODE -eq 0 -and $ok -eq "True") { return ,$c }
        } catch { }
    }
    return $null
}

$Venv = Join-Path $Here ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Py)) {
    $base = Find-Python
    if ($null -eq $base) {
        Write-Host "Hotpath needs Python 3.11 or newer: https://www.python.org/downloads/ (tick 'Add to PATH')"
        exit 1
    }
    Write-Host "[setup] creating $Venv"
    $exe = $base[0]; $rest = @($base | Select-Object -Skip 1)
    & $exe @rest -m venv $Venv
    if ($LASTEXITCODE -ne 0) { exit 1 }
}

$Stamp = Join-Path $Venv ".hotpath-installed"
$Pyproject = Join-Path $Here "pyproject.toml"
$needInstall = -not (Test-Path $Stamp)
if (-not $needInstall) { $needInstall = (Get-Item $Pyproject).LastWriteTime -gt (Get-Item $Stamp).LastWriteTime }
if ($needInstall) {
    Write-Host "[setup] installing Hotpath into $Venv (first run only)"
    & $Py -m pip install --disable-pip-version-check -q -e $Here
    if ($LASTEXITCODE -ne 0) { Write-Host "installing Hotpath failed"; exit 1 }
    Set-Content -Path $Stamp -Value (Get-Date -Format o)
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "git is not installed: https://git-scm.com/download/win"
    exit 1
}

Push-Location $Here
try {
    $env:PYTHONUTF8 = "1"
    & $Py -m hotpath.cli go @Forward
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code

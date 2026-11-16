$ErrorActionPreference = 'Stop'
$probe = $PSScriptRoot
$logs = Join-Path $probe 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$log = Join-Path $logs 'startup.log'
try {
    & (Join-Path $probe 'setup_env.ps1') -Quick *> $log
    if ($LASTEXITCODE -ne 0) {
        & (Join-Path $probe 'setup_env.ps1') *>> $log
        if ($LASTEXITCODE -ne 0) { throw 'Environment repair failed. See startup.log or run the environment repair BAT.' }
    }
    $candidates = @()
    $pythonPath = Join-Path $probe 'python-path.txt'
    if (Test-Path -LiteralPath $pythonPath) {
        $candidates += (Get-Content -LiteralPath $pythonPath -Raw).Trim()
    }
    $candidates += (Join-Path $probe 'python\python.exe')
    foreach ($command in @('python.exe','py.exe')) {
        $found = Get-Command $command -ErrorAction SilentlyContinue
        if ($found) {
            $resolved = if ($command -eq 'py.exe') { & $found.Source -3 -c 'import sys;print(sys.executable)' 2>$null } else { & $found.Source -c 'import sys;print(sys.executable)' 2>$null }
            if ($LASTEXITCODE -eq 0) { $candidates += $resolved }
        }
    }
    $python = $null
    foreach ($candidate in $candidates) {
        if (!$candidate -or !(Test-Path -LiteralPath $candidate)) { continue }
        & $candidate -c 'import sys' *> $null
        if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
    }
    if (!$python) { throw 'Python was not found. Run the environment repair BAT.' }
    & $python (Join-Path $probe 'upgrade_helper.py') *>> $log
    $pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
    if (!(Test-Path -LiteralPath $pythonw)) { $pythonw = $python }
    Start-Process -FilePath $pythonw -ArgumentList ('"{0}"' -f (Join-Path $probe 'app.py')) -WorkingDirectory $probe -WindowStyle Hidden
} catch {
    $_ | Out-String | Add-Content -LiteralPath $log
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(("Startup failed.`r`n{0}`r`nLog: {1}" -f $_.Exception.Message,$log),'Shadowverse assistant') | Out-Null
    exit 1
}

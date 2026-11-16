$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
  Where-Object { $_.CommandLine -like '*download*' } |
  ForEach-Object {
    Write-Output ("kill " + $_.ProcessId)
    Stop-Process -Id $_.ProcessId -Force
  }
Write-Output "done"

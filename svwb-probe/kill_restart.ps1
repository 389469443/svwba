$ErrorActionPreference = 'SilentlyContinue'
Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
  Where-Object { $_.CommandLine -like '*download*' } |
  ForEach-Object {
    Write-Output ("kill " + $_.ProcessId)
    Stop-Process -Id $_.ProcessId -Force
  }
Start-Sleep -Seconds 1
Write-Output "restart"
Start-Process -FilePath "cmd.exe" -ArgumentList '/c','"D:\download\影之幻境助手-v0.1.0\启动.bat"'

@echo off
chcp 936 >nul 2>nul
cd /d "%~dp0"
title 影之诗记牌器 手牌探针

rem 游戏是以管理员权限运行的；Frida 要挂进游戏，探针也必须提权
net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo   游戏是用管理员权限跑的，探针也必须一样，否则 Frida 会 PermissionDenied。
  echo   正在请求管理员权限，请在弹窗里点“是”……
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
if not exist "%PY%" set "PY=C:\Python312\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo.
  echo   找不到能用的 Python。先双击“修复环境.bat”，再回来运行本文件。
  pause
  exit /b 1
)

echo   使用解释器：%PY%
echo   开好游戏、进到对局里，探针会自动抓数据；打完一局后关掉这个窗口即可。
echo.
"%PY%" "%~dp0手牌探针.py" --sec 900
echo.
echo   日志已写入：%~dp0logs\手牌探针.log
pause

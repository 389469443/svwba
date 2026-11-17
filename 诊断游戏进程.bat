@echo off
rem 进程诊断：不读内存、不注入，只列出进程并说明程序会选谁。
chcp 936 >nul 2>nul
cd /d "%~dp0"
title 影之诗助手 - 进程诊断

echo ============================================================
echo    正在检查「游戏进程能不能被认出来」
echo    请先把模拟器和游戏都打开、进到对局里，再双击本文件
echo ============================================================
echo.

set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python312"
if not exist "%PY%" set "PY=python"

"%PY%" -u "%~dp0svwb-probe\诊断游戏进程.py" --pause
if errorlevel 1 (
  echo.
  echo 如果上面提示找不到 Python，请先双击「启动.bat」把环境装好再来。
  pause
)

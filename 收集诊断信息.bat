@echo off
rem 强制中文代码页
chcp 936 >nul 2>nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title 影之幻境助手 · 收集诊断信息

echo ============================================================
echo    收集诊断信息
echo   ----------------------------------------------------------
echo    会生成一个 zip（日志 / 数据 / 对照截图 / 游戏日志 / 环境）
echo    只读，不改任何设置，不碰游戏
echo ============================================================
echo.

set "PROBE=%~dp0svwb-probe"
set "PY="
if exist "%PROBE%\python-path.txt" (
  set /p CAND=<"%PROBE%\python-path.txt"
  if exist "!CAND!" set "PY=!CAND!"
)
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo 【错误】找不到 Python，请先双击「修复环境.bat」。
  pause
  exit /b 1
)

%PY% "%PROBE%\collect_diag.py"
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" (
  echo 收集失败，请把上面的错误信息截图发给作者。
)
pause
exit /b %RC%

@echo off
rem 强制中文代码页，避免乱码（见使用说明 Q1）
chcp 936 >nul 2>nul
rem ============================================================
rem  影之幻境助手 · 修复运行环境
rem  重新检查 Python / Node.js / 依赖库，缺什么装什么。
rem  已经装好的会跳过，不会重复下载。
rem ============================================================
setlocal
cd /d "%~dp0"
title 影之幻境助手 · 修复运行环境

echo ============================================================
echo    修复运行环境
echo   ----------------------------------------------------------
echo    正在检查 Python / Node.js / 依赖库……
echo    已经装好的会跳过；缺的会尝试自动安装（需要联网）。
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0svwb-probe\setup_env.ps1" -Force
set "RC=%errorlevel%"

echo.
if "%RC%"=="0" (
  echo 环境就绪。现在可以双击「启动.bat」开始使用。
) else (
  echo 没能自动装好。请按上面的提示手动处理，
  echo 或者把整个窗口内容截图发给我。
)
echo.
pause
exit /b %RC%

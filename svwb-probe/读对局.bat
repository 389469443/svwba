@echo off
chcp 65001 >nul
setlocal

set PY=C:\Users\33949\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe
set PROBE=C:\Users\33949\Documents\deepseek-harness\default-workspace\svwb-probe

title 影之幻境 · 只读对局读取器

echo ============================================================
echo   影之幻境 · 只读对局读取器
echo   ------------------------------------------------------------
echo   读取：双方幻境等级 / 血量 / 护甲 / 场上随从（星级·攻·血）
echo   方式：OpenProcess + ReadProcessMemory（**只读**）
echo         不写入、不注入、不 hook、不模拟输入
echo   前提：游戏已启动，且**处于战斗阶段**
echo   ------------------------------------------------------------
echo   输出会自动保存到 svwb-probe\logs\运行-时间.txt
echo   跑完（或看够了按 Ctrl+C）后，跟助手说一句「好了」即可。
echo ============================================================
echo.

if not exist "%PY%" (
  echo [错误] 找不到 Python：%PY%
  pause
  exit /b 1
)

"%PY%" "%PROBE%\run_and_log.py"

echo.
pause

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成启动.bat —— GBK 编码 + 可靠的 Python 探测。

两个坑都要处理：

1) **编码**：cmd.exe 在中文 Windows 上按 GBK 逐行解析批处理。
   存成 UTF-8 会把中文读成乱码，乱码再被当命令执行，出现
   `'闈磋◆鑿...' 不是内部或外部命令`。文件里写 chcp 65001 也救不了。
   → 所以本文件用 GBK 无 BOM 写出，且只用 GBK 能表示的字符。

2) **假的 python**：Windows 10/11 自带一个应用商店占位程序
   `%LOCALAPPDATA%\\Microsoft\\WindowsApps\\python.exe`，它**不执行 Python**，
   直接返回 9009。如果只 `where python` 就选中它，双击 bat 会一闪而过。
   → 所以每个候选都必须用 `-c "import sys"` **实测通过**才采用。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_启动_v010.bat")

BAT = r"""@echo off
setlocal enabledelayedexpansion

set ROOT=%~dp0
set PROBE=%ROOT%svwb-probe

title 影之幻境助手 v0.1.0

echo ============================================================
echo    影之幻境助手   v0.1.0
echo   ----------------------------------------------------------
echo    功能：开战瞬间读取双方阵容，给出胜率与掉血概率
echo          并记录每场战绩（主战者 / 阵容 / 实际掉血 / 名次）
echo   ----------------------------------------------------------
echo    数据来源：只读游戏进程内存
echo              不写内存 / 不注入 / 不 hook / 不模拟输入
echo   ----------------------------------------------------------
echo    【重要】游戏请用「窗口模式」或「无边框窗口」
echo             全屏独占时系统不允许其它窗口盖在上面，
echo             悬浮层和战绩面板会被挡住、看不见。
echo ============================================================
echo.

rem ============================================================
rem  查找真正能用的 Python
rem  每个候选都要用 -c "import sys" 实测，占位程序会返回 9009
rem ============================================================
set PY=
set PYARGS=

rem ---- 1) 用户手动指定：python-path.txt 里写完整路径 ----
if exist "%PROBE%\python-path.txt" (
  set /p CAND=<"%PROBE%\python-path.txt"
  if exist "!CAND!" (
    "!CAND!" -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      set PY="!CAND!"
      echo [1] 使用手动指定的 Python
    )
  )
)

rem ---- 2) py 启动器（Windows 官方，最可靠）----
if not defined PY (
  for /f "delims=" %%i in ('where py 2^>nul') do (
    if not defined PY (
      "%%i" -3 -c "import sys" >nul 2>nul
      if not errorlevel 1 (
        set PY="%%i"
        set PYARGS=-3
        echo [2] 使用 py 启动器
      )
    )
  )
)

rem ---- 3) PATH 里的 python（可能命中商店占位程序，必须实测）----
if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PY (
      "%%i" -c "import sys" >nul 2>nul
      if not errorlevel 1 (
        set PY="%%i"
        echo [3] 使用 PATH 中的 python
      )
    )
  )
)

rem ---- 4) 常见安装位置 ----
if not defined PY (
  for %%D in (
    "C:\Python312" "C:\Python311" "C:\Python310"
    "%LOCALAPPDATA%\Programs\Python\Python312"
    "%LOCALAPPDATA%\Programs\Python\Python311"
    "%LOCALAPPDATA%\Programs\Python\Python310"
    "%ProgramFiles%\Python312" "%ProgramFiles%\Python311"
  ) do (
    if not defined PY (
      if exist "%%~D\python.exe" (
        "%%~D\python.exe" -c "import sys" >nul 2>nul
        if not errorlevel 1 (
          set PY="%%~D\python.exe"
          echo [4] 使用 %%D
        )
      )
    )
  )
)

rem ---- 5) 随包放置的便携版 ----
if not defined PY (
  if exist "%PROBE%\python\python.exe" (
    "%PROBE%\python\python.exe" -c "import sys" >nul 2>nul
    if not errorlevel 1 (
      set PY="%PROBE%\python\python.exe"
      echo [5] 使用随包便携版 Python
    )
  )
)

if not defined PY (
  echo.
  echo 【错误】没有找到可用的 Python。
  echo.
  echo   注意：如果只装了应用商店里的 Python 占位程序，是不行的。
  echo   请到 https://www.python.org/downloads/ 安装正式版，
  echo   安装时务必勾选 "Add python.exe to PATH"。
  echo.
  echo   装好后可以这样验证（应输出版本号）：
  echo       py -3 --version
  echo.
  echo   实在不行：把 python.exe 的完整路径写进
  echo       %PROBE%\python-path.txt
  echo   然后重新双击本文件。
  echo.
  pause
  exit /b 1
)

echo.
echo 使用解释器：%PY% %PYARGS%
echo 正在启动控制台……
start "" /min %PY% %PYARGS% "%PROBE%\app.py"

echo.
echo 控制台已启动，悬浮层与战绩面板会自动跟着起来。
echo 关闭控制台窗口即可退出全部。
echo.
timeout /t 3 >nul
"""


def main():
    data = BAT.replace("\n", "\r\n")
    with open(OUT, "w", encoding="gbk", newline="") as fh:
        fh.write(data)

    raw = open(OUT, "rb").read()
    print(f"已写出 {OUT}")
    print(f"  大小    : {len(raw)} B")
    print(f"  前4字节 : {raw[:4].hex(' ')}")
    print("  BOM     :", "有（必须去掉）" if raw[:3] == b"\xef\xbb\xbf" else "无 ✓")
    with open(OUT, encoding="gbk") as fh:
        back = fh.read()
    ok = back.startswith("@echo off") and "影之幻境助手" in back
    print("  GBK 读回:", "OK ✓" if ok else "失败")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

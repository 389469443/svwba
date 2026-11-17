@echo off
rem 强制中文代码页：避免系统开了「UTF-8 beta」时中文全乱（见使用说明 Q1）
chcp 936 >nul 2>nul
setlocal enabledelayedexpansion

set ROOT=%~dp0
set PROBE=%ROOT%svwb-probe
rem 随包 Node.js（首次运行时自动下载到这里），让判定器起的 node 子进程能找到
if exist "%PROBE%\node\node.exe" set "PATH=%PROBE%\node;%PATH%"

title 影之诗助手汉萨改 V1.1.2

echo ============================================================
echo    影之诗助手汉萨改   V1.1.2
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
rem  每次启动都先快速自检（-Quick：静默、不安装，约 0.5~1 秒）
rem    环境齐全 → 直接往下走（跳过安装）
rem    缺任何一样 → 跑完整的检查 / 安装流程（会显示进度）
rem ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROBE%\setup_env.ps1" -Quick >nul 2>nul
if errorlevel 1 (
  echo.
  echo ============================================================
  echo    运行环境不完整：正在自动安装
  echo      Python / Node.js / 依赖库，缺什么装什么
  echo    需要联网，可能要几分钟；装好以后就直接启动
  echo ============================================================
  echo.
  powershell -NoProfile -ExecutionPolicy Bypass -File "%PROBE%\setup_env.ps1"
  if errorlevel 1 (
    echo.
    echo 【环境没有装好】请看上面的提示。
    echo   也可以双击「修复环境.bat」重试，或按提示手动安装。
    echo   如果这里连 powershell 都跑不起来，说明被系统策略拦了，
    echo   请手动装 Python / Node.js（见使用说明 Q2）。
    echo.
    pause
    exit /b 1
  )
  echo.
)

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
  echo   也可以直接双击「修复环境.bat」，让它自动装。
  echo.
  pause
  exit /b 1
)

echo.
echo 使用解释器：%PY% %PYARGS%

rem ── 无窗口启动：优先用 pythonw.exe（没有就退回 python，会多一个黑框）──
set "PYEXE=%PY%"
set "PYEXE=%PYEXE:"=%"
set "PYW="
for %%I in ("%PYEXE%") do (
  if /i "%%~nxI"=="python.exe" if exist "%%~dpIpythonw.exe" set "PYW=%%~dpIpythonw.exe"
  if /i "%%~nxI"=="py.exe" if exist "%%~dpIpyw.exe" set "PYW=%%~dpIpyw.exe"
)

if /i "%~1"=="/dry" (
  echo DRY PYW=!PYW! PY=%PY% ARGS=%PYARGS%
  exit /b 0
)

if defined PYW (
  start "" "!PYW!" %PYARGS% "%PROBE%\app.py"
) else (
  echo （没有 pythonw.exe，只能带窗口启动）
  start "" /min %PY% %PYARGS% "%PROBE%\app.py"
)

echo.
echo 控制台已启动（后台运行，不会留黑框），悬浮层与战绩面板会自动跟着起来。
echo 托盘图标可双击显示控制台；右键菜单可以退出。
echo.
rem 短暂等待后关闭本窗口（stdin 被重定向时 timeout 会报错，退回 ping）
timeout /t 1 /nobreak >nul 2>nul || ping -n 2 127.0.0.1 >nul 2>nul
exit /b 0

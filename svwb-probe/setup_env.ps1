# -*- coding: utf-8 -*-
<#
  影之幻境助手 · 运行环境自动检查 / 安装

  做什么
  ------
  1. 找 Python（3.10+）：python-path.txt 指定的 → py 启动器 → PATH → 常见安装目录 → 随包 python\
  2. 找 Node.js（18+）：随包 node\ → node-path.txt → PATH → 常见安装目录
  3. 找依赖库：Pillow / NumPy（缺了就 pip install，官方源失败自动换清华镜像）
  4. 缺什么装什么：Python 用 winget 或官方安装包；Node 用官方 zip（免管理员）
  5. 全部就绪 → 写 .env-ready.json 记录（只做记录，不靠它跳过检查）

  用法
  ----
    powershell -NoProfile -ExecutionPolicy Bypass -File setup_env.ps1
        检查 + 缺什么装什么（启动.bat 在 -Quick 探测到缺东西后调这个）
    ... -Quick
        只做**最快探测**：齐全就静默 exit 0（约 0.5~1 秒），缺任何一样 exit 1，不打印、不安装。
        启动.bat 每次启动都先跑它 —— 环境齐全直接跳过，缺了才走安装流程。
    ... -Force
        强制重新检查一遍（修复环境.bat 用），同样"缺了才装"
    ... -Check
        只检查、不安装（排查用，退出码 0=就绪 / 1=缺东西）

  退出码：0 就绪 / 1 一般错误 / 2 缺 Python / 3 缺 Node.js / 4 缺依赖库
#>
param(
    [switch]$Check,
    [switch]$Force,
    [switch]$Quiet,
    [switch]$Quick
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'          # 下载进度条会拖慢 Invoke-WebRequest

$Probe    = $PSScriptRoot                          # …\svwb-probe
$Root     = Split-Path -Parent $Probe              # 包根
$Marker   = Join-Path $Probe '.env-ready.json'
$PyPath   = Join-Path $Probe 'python-path.txt'
$NodePath = Join-Path $Probe 'node-path.txt'
$LogDir   = Join-Path $Probe 'logs'
$LogFile  = Join-Path $LogDir '环境检查.log'
$NodeDir  = Join-Path $Probe 'node'                # 随包 node（zip 解压到这里）

$NodeZipVersion = 'v20.18.0'
$PyInstallerUrl = 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe'
# 国内镜像（实测：本机 nodejs.org 直接超时拿不到一个字节，npmmirror 秒开）。
# 顺序：官网 → 镜像；每个都设了超时，卡住不会无限等。
$PyMirrorUrl = 'https://mirrors.huaweicloud.com/python/3.12.7/python-3.12.7-amd64.exe'
$NodeZipMirror = 'https://npmmirror.com/mirrors/node'

function Say($msg, $color) {
    if (-not $Quiet) {
        if ($color) { Write-Host $msg -ForegroundColor $color } else { Write-Host $msg }
    }
}
function Log($msg) {
    try {
        if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
        $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    } catch { }
}
function Fatal($code, $msg) {
    Say '' $null
    Say $msg 'Red'
    Log ("失败：{0}" -f $msg)
    exit $code
}

# ── 网络：强制 TLS 1.2（Python/Node 官网都不接受 TLS 1.0）────────────────
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

function URLOrder($primary, $mirror) {
    # 中文系统先走国内镜像：官网在国内容易"连得上但极慢"
    # （实测 nodejs.org 90 秒只下了 1.2MB，npmmirror 一分钟就拿完）。
    try {
        if ((Get-Culture).Name -like 'zh-*') { return @($mirror, $primary) }
    } catch { }
    return @($primary, $mirror)
}

function Download($urls, $out) {
    # $urls：按顺序尝试（中文系统：镜像 → 官网；其它：官网 → 镜像）。
    # ⚠️ 优先用 **curl.exe**（Win10 1803+ 自带）：
    #    Invoke-WebRequest 的 -TimeoutSec 在"连上了但一个字节都收不到"时不生效，
    #    实测卡了 15 分钟还没返回；curl 的 --connect-timeout 能立刻失败切镜像。
    $curl = $null
    $cc = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($cc) { $curl = $cc.Source }
    if (-not $curl) {
        $cc = Get-Command curl -ErrorAction SilentlyContinue
        if ($cc -and $cc.CommandType -eq 'Application') { $curl = $cc.Source }
    }
    foreach ($u in $urls) {
        $maxTime = 600
        if ($u -like 'https://nodejs.org/*' -or $u -like 'https://www.python.org/*') { $maxTime = 60 }
        Say ("    下载（最多 {0}s）：{1}" -f $maxTime, $u) 'DarkGray'
        Say '      （约 25~30MB，网慢的话要 1~2 分钟，请等它跑完）' 'DarkGray'
        Log ("下载 {0} -> {1}（最多 {2}s）" -f $u, $out, $maxTime)
        if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
        $okUrl = $false
        if ($curl) {
            # ⚠️ 脚本顶部是 $ErrorActionPreference='Stop'，而原生程序写 stderr 在 PS 5.1 里
            #    会被当成 ErrorRecord -> 直接终止脚本（实测：curl 超时写了 stderr，
            #    整个安装器立刻退出，镜像都没来得及试）。所以这里临时放宽。
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'SilentlyContinue'
            try {
                & $curl -L --silent --show-error --fail --connect-timeout 15 `
                    --max-time $maxTime -o $out $u 2>$null | Out-Null
                $rc = $LASTEXITCODE
            } finally {
                $ErrorActionPreference = $prevEAP
            }
            if ($rc -eq 0) {
                $okUrl = $true
            } else {
                Say ("    这个地址不行（curl 退出码 {0}）" -f $rc) 'Yellow'
                Log ("下载失败：curl 退出码 {0}，地址 {1}" -f $rc, $u)
            }
        } else {
            try {
                Invoke-WebRequest -Uri $u -OutFile $out -UseBasicParsing -TimeoutSec $maxTime
                $okUrl = $true
            } catch {
                Say ("    这个地址不行：{0}" -f $_.Exception.Message) 'Yellow'
                Log ("下载失败：{0}" -f $_.Exception.Message)
            }
        }
        if ($okUrl -and (Test-Path -LiteralPath $out) -and (Get-Item -LiteralPath $out).Length -gt 0) {
            Say '    下载完成' 'Green'
            return $true
        }
    }
    return $false
}

# ── Python ──────────────────────────────────────────────────────────────
function Get-PyVersion($exe, $pre) {
    # ⚠️ 不用 `-c "import sys;print('%d.%d.%d'%...)"`：
    #    PowerShell 5.1 往原生 exe 传参时会把 -c 里的**双引号吃掉**，
    #    Python 直接 SyntaxError 退出 1 → 被误判成"没有 Python"。
    #    改成 `-V`（输出 "Python 3.12.14"），不涉及任何引号。
    if (-not $exe) { return $null }
    if (-not (Test-Path -LiteralPath $exe)) { return $null }
    $ver = $null
    try {
        $args2 = @()
        if ($pre) { $args2 += $pre }
        $args2 += @('-V')
        $out = & $exe @args2 2>&1
        if ($LASTEXITCODE -eq 0 -and $out) {
            $txt = ($out | Select-Object -Last 1).ToString().Trim()
            if ($txt -match 'Python\s+(\d+)\.(\d+)\.(\d+)') {
                $ver = '{0}.{1}.{2}' -f $Matches[1], $Matches[2], $Matches[3]
            }
        }
    } catch { $ver = $null }
    if (-not $ver) { return $null }
    $parts = $ver.Split('.')
    if ($parts.Length -lt 2) { return $null }
    $major = [int]$parts[0]; $minor = [int]$parts[1]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) { return $null }
    return $ver
}

function Find-Python {
    # ① python-path.txt（用户手动指定过，或上次自动装好后写进去的）
    if (Test-Path -LiteralPath $PyPath) {
        try {
            $c = (Get-Content -LiteralPath $PyPath -TotalCount 1).Trim().Trim('"')
            if ($c) {
                $v = Get-PyVersion $c $null
                if ($v) { return [pscustomobject]@{ exe = $c; pre = @(); version = $v; from = 'python-path.txt' } }
            }
        } catch { }
    }
    # ② py 启动器（Windows 官方安装器带的，能绕开商店占位程序）
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $v = Get-PyVersion $py.Source '-3'
        if ($v) { return [pscustomobject]@{ exe = $py.Source; pre = @('-3'); version = $v; from = 'py -3' } }
    }
    # ③ PATH 里的 python（商店占位程序会失败，Get-PyVersion 自己会剔掉）
    foreach ($c in @('python', 'python3')) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd) {
            $v = Get-PyVersion $cmd.Source $null
            if ($v) { return [pscustomobject]@{ exe = $cmd.Source; pre = @(); version = $v; from = 'PATH' } }
        }
    }
    # ④ 随包 python\（如果有便携版）
    $localPy = Join-Path $Probe 'python\python.exe'
    if (Test-Path -LiteralPath $localPy) {
        $v = Get-PyVersion $localPy $null
        if ($v) { return [pscustomobject]@{ exe = $localPy; pre = @(); version = $v; from = '随包 python\' } }
    }
    # ⑤ 常见安装位置
    $known = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python310\python.exe'),
        'C:\Python313\python.exe', 'C:\Python312\python.exe',
        'C:\Python311\python.exe', 'C:\Python310\python.exe'
    )
    foreach ($c in $known) {
        $v = Get-PyVersion $c $null
        if ($v) { return [pscustomobject]@{ exe = $c; pre = @(); version = $v; from = '常见目录' } }
    }
    return $null
}

function Install-Python {
    Say '  没有找到可用的 Python，尝试自动安装……' 'Yellow'
    Log 'Python 缺失，开始自动安装'
    # ① 先试 winget（有就用，装的是官方包）
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Say '  ① 用 winget 安装 Python 3.12（可能会弹一次权限确认）……' 'Cyan'
        foreach ($id in @('Python.Python.3.12', 'Python.Python.3.11')) {
            try {
                & winget install --id $id -e --source winget --silent `
                    --accept-package-agreements --accept-source-agreements 2>$null | Out-Null
            } catch { }
            $p = Find-Python
            if ($p) { return $p }
        }
    }
    # ② 官方安装包（按用户安装，不需要管理员）
    Say '  ② 用 Python 官网安装包（按当前用户安装，不需要管理员）……' 'Cyan'
    $out = Join-Path $env:TEMP 'python-setup.exe'
    if (Download (URLOrder $PyInstallerUrl $PyMirrorUrl) $out) {
        try {
            $p = Start-Process -FilePath $out -Wait -PassThru -ArgumentList @(
                '/quiet', 'InstallAllUsers=0', 'PrependPath=1',
                'Include_pip=1', 'Include_test=0', 'Include_launcher=1')
            Log ("Python 安装器退出码 {0}" -f $p.ExitCode)
        } catch {
            Log ("Python 安装失败：{0}" -f $_.Exception.Message)
        }
    }
    return (Find-Python)
}

# ── Node.js ─────────────────────────────────────────────────────────────
function Get-NodeVersion($exe) {
    if (-not $exe -or -not (Test-Path -LiteralPath $exe)) { return $null }
    try {
        $out = & $exe -v 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $v = $out.ToString().Trim()          # 形如 v20.18.0
        if ($v -notmatch '^v(\d+)\.') { return $null }
        if ([int]$Matches[1] -lt 18) { return $null }
        return $v
    } catch { return $null }
}

function Find-Node {
    # ① 随包 node\node.exe（安装器解压到这）
    $localNode = Join-Path $NodeDir 'node.exe'
    $v = Get-NodeVersion $localNode
    if ($v) { return [pscustomobject]@{ exe = $localNode; version = $v; from = '随包 node\' } }
    # ② node-path.txt
    if (Test-Path -LiteralPath $NodePath) {
        try {
            $c = (Get-Content -LiteralPath $NodePath -TotalCount 1).Trim().Trim('"')
            $v = Get-NodeVersion $c
            if ($v) { return [pscustomobject]@{ exe = $c; version = $v; from = 'node-path.txt' } }
        } catch { }
    }
    # ③ PATH
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd) {
        $v = Get-NodeVersion $cmd.Source
        if ($v) { return [pscustomobject]@{ exe = $cmd.Source; version = $v; from = 'PATH' } }
    }
    # ④ 常见安装位置
    $pf86 = ${env:ProgramFiles(x86)}
    $knownNode = @((Join-Path $env:ProgramFiles 'nodejs\node.exe'))
    if ($pf86) { $knownNode += (Join-Path $pf86 'nodejs\node.exe') }
    foreach ($c in $knownNode) {
        $v = Get-NodeVersion $c
        if ($v) { return [pscustomobject]@{ exe = $c; version = $v; from = '常见目录' } }
    }
    return $null
}

function Install-Node {
    Say '  没有找到 Node.js，尝试自动安装……' 'Yellow'
    Log 'Node.js 缺失，开始自动安装'
    # ① 官方 zip（免管理员、免改 PATH，解压到 svwb-probe\node\）
    #    官网在国内经常直接超时（实测 12 秒拿不到一个字节），所以带 npmmirror 镜像
    $zipUrl = "https://nodejs.org/dist/$NodeZipVersion/node-$NodeZipVersion-win-x64.zip"
    $zipMirror = "$NodeZipMirror/$NodeZipVersion/node-$NodeZipVersion-win-x64.zip"
    $zip = Join-Path $env:TEMP ("node-{0}-win-x64.zip" -f $NodeZipVersion)
    if (Download (URLOrder $zipUrl $zipMirror) $zip) {
        $tmp = Join-Path $env:TEMP ('node-unzip-' + [guid]::NewGuid().ToString('N'))
        try {
            Say '    解压……' 'DarkGray'
            Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force
            $inner = Get-ChildItem -LiteralPath $tmp -Directory | Select-Object -First 1
            if ($inner) {
                if (Test-Path -LiteralPath $NodeDir) { Remove-Item -LiteralPath $NodeDir -Recurse -Force }
                Move-Item -LiteralPath $inner.FullName -Destination $NodeDir -Force
                Log ("Node 已解压到 {0}" -f $NodeDir)
            }
        } catch {
            Log ("解压 Node 失败：{0}" -f $_.Exception.Message)
        } finally {
            Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
        }
        $n = Find-Node
        if ($n) { return $n }
    }
    # ② 退而求其次：winget
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Say '  ② 用 winget 安装 Node.js LTS……' 'Cyan'
        try {
            & winget install --id OpenJS.NodeJS.LTS -e --source winget --silent `
                --accept-package-agreements --accept-source-agreements 2>$null | Out-Null
        } catch { }
    }
    return (Find-Node)
}

# ── Python 依赖库 ───────────────────────────────────────────────────────
function Test-PyModule($py, $mod) {
    try {
        $a = @()
        if ($py.pre) { $a += $py.pre }
        $a += @('-c', "import $mod")
        & $py.exe @a 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Ensure-PyDeps($py) {
    $need = @()
    foreach ($m in @(@{ mod = 'PIL'; pkg = 'pillow' }, @{ mod = 'numpy'; pkg = 'numpy' })) {
        if (-not (Test-PyModule $py $m.mod)) { $need += $m.pkg }
    }
    if ($need.Count -eq 0) { return $true }
    Say ("  缺依赖库：{0}，开始安装……" -f ($need -join ' / ')) 'Yellow'
    Log ("缺依赖：{0}" -f ($need -join ','))
    $base = @()
    if ($py.pre) { $base += $py.pre }
    # 第一次：官方源
    $a = $base + @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input') + $need
    try { & $py.exe @a 2>$null | Out-Null } catch { }
    $ok = $true
    foreach ($m in @(@{ mod = 'PIL'; pkg = 'pillow' }, @{ mod = 'numpy'; pkg = 'numpy' })) {
        if (-not (Test-PyModule $py $m.mod)) { $ok = $false }
    }
    if ($ok) { return $true }
    # 第二次：清华镜像（国内网络常见）
    Say '    官方源不可用，换清华镜像重试……' 'Cyan'
    Log 'pip 官方源失败，改用清华镜像'
    $a = $base + @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input',
                   '-i', 'https://pypi.tuna.tsinghua.edu.cn/simple') + $need
    try { & $py.exe @a 2>$null | Out-Null } catch { }
    foreach ($m in @(@{ mod = 'PIL'; pkg = 'pillow' }, @{ mod = 'numpy'; pkg = 'numpy' })) {
        if (-not (Test-PyModule $py $m.mod)) { return $false }
    }
    return $true
}

function Get-PipVersion($py, $pkg) {
    # 用 `pip show` 取版本（同样避开 -c 里的引号问题）
    try {
        $a = @()
        if ($py.pre) { $a += $py.pre }
        $a += @('-m', 'pip', 'show', $pkg)
        $out = & $py.exe @a 2>$null
        foreach ($line in $out) {
            if ($line -match '^Version:\s*(.+)$') { return $Matches[1].Trim() }
        }
    } catch { }
    return '?'
}

# ── 主流程 ──────────────────────────────────────────────────────────────
if ($Quick) {
    # 启动.bat 每次启动都会先跑这个：**静默**、只探测、不安装。
    #   齐全 → exit 0（约 0.5~1 秒，用户感觉就是"秒开"）
    #   缺任何一样 → exit 1，启动.bat 再去跑完整的安装流程（那时才有输出）
    $qpy = Find-Python
    if (-not $qpy) { exit 1 }
    $qnode = Find-Node
    if (-not $qnode) { exit 1 }
    # 依赖库合并成一次 python 启动检查（省 0.2 秒）
    try {
        $a = @()
        if ($qpy.pre) { $a += $qpy.pre }
        $a += @('-c', 'import PIL, numpy')
        & $qpy.exe @a 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { exit 1 }
    } catch { exit 1 }
    exit 0
}

Say '============================================================' 'DarkGray'
Say '  影之幻境助手 · 运行环境检查' 'Cyan'
Say '============================================================' 'DarkGray'
Log '==== 环境检查开始 ===='

$py = Find-Python
if (-not $py) {
    if ($Check) { Fatal 2 '缺少 Python 3.10+（只检查模式，未安装）' }
    $py = Install-Python
    if (-not $py) {
        Fatal 2 ("没能自动安装 Python。请手动安装：{0}`n安装时勾选 Add python.exe to PATH，装好后再双击「修复环境.bat」。" -f 'https://www.python.org/downloads/')
    }
}
Say ("  Python：{0}  {1}  [{2}]" -f $py.exe, $py.version, $py.from) 'Green'

$node = Find-Node
if (-not $node) {
    if ($Check) { Fatal 3 '缺少 Node.js 18+（只检查模式，未安装）' }
    $node = Install-Node
    if (-not $node) {
        Fatal 3 ("没能自动安装 Node.js。请手动安装 LTS 版：{0}，装好后再双击「修复环境.bat」。" -f 'https://nodejs.org/zh-cn/download')
    }
}
Say ("  Node.js：{0}  {1}  [{2}]" -f $node.exe, $node.version, $node.from) 'Green'

if ($Check) {
    foreach ($m in @(@{ mod = 'PIL'; pkg = 'Pillow' }, @{ mod = 'numpy'; pkg = 'NumPy' })) {
        if (Test-PyModule $py $m.mod) {
            Say ("  {0}：已安装" -f $m.pkg) 'Green'
        } else {
            Say ("  {0}：缺失" -f $m.pkg) 'Red'
            exit 4
        }
    }
    Say '环境就绪（只检查模式，未做任何安装）' 'Cyan'
    Log '只检查模式：环境就绪'
    exit 0
}

if (-not (Ensure-PyDeps $py)) {
    Fatal 4 ("依赖库没装好。请手动执行：`n  `"{0}`" -m pip install pillow numpy" -f $py.exe)
}
Say '  Pillow / NumPy：已安装' 'Green'

# 写回 python-path.txt / node-path.txt，让 启动.bat 和 Python 侧都能找到
#
# ⚠️ 编码有讲究：
#   · python-path.txt 是**批处理**用 `set /p` 读的 → 必须用系统 ANSI（无 BOM），
#     否则 BOM 会被当成路径的一部分；中文路径在中文系统上也能正常工作。
#   · node-path.txt 是 **Python** 读的 → UTF-8 无 BOM。
try {
    Set-Content -LiteralPath $PyPath -Value $py.exe -Encoding Default
    [IO.File]::WriteAllText($NodePath, $node.exe, (New-Object Text.UTF8Encoding($false)))
} catch {
    Log ("写 python-path.txt / node-path.txt 失败：{0}" -f $_.Exception.Message)
}

# 就绪标记：下次启动直接跳过检查
try {
    $info = [ordered]@{
        ready          = $true
        at             = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
        python         = $py.exe
        python_version = $py.version
        node           = $node.exe
        node_version   = $node.version
        pillow         = (Get-PipVersion $py 'pillow')
        numpy          = (Get-PipVersion $py 'numpy')
        script         = $MyInvocation.MyCommand.Path
    }
    ($info | ConvertTo-Json) | Set-Content -LiteralPath $Marker -Encoding UTF8
    Log ("已写就绪标记 {0}" -f $Marker)
} catch {
    Log ("写就绪标记失败：{0}" -f $_.Exception.Message)
}

Say '' $null
Say '  环境全部就绪，正在启动……' 'Cyan'
Log '环境就绪'
exit 0

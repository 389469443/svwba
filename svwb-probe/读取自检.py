#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
读取自检 —— 只读，不写内存 / 不注入 / 不 hook / 不改任何文件。

用途：影之诗助手读不到游戏内容时，一条命令定位"卡在哪一步"：
  1. 运行环境（Python / numpy / frida）
  2. 进程扫描（哪些进程像游戏，为什么被过滤掉）
  3. 命中进程 + OpenProcess 权限（错误码 5 = 权限不一致，要管理员）
  4. 目标进程有没有加载 GameAssembly.dll（记牌器/Frida 依赖它）
  5. il2cpp 类定位（Sv2Warchess* 类还在不在 → 判断游戏版本是否改了类名）
  6. 实例扫描 + 玩家字段是否合理（life/grade 是否在正常范围 → 判断偏移是否错位）
  7. 记牌器依赖 frida 是否可用

用法（在 svwb-probe 目录里，用助手自己的 Python 跑）：
    python 读取自检.py
    python 读取自检.py --frida      额外测试 frida 能否 attach（不加载脚本）
"""
import ctypes as C
import ctypes.wintypes as W
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import shadow_warchess_reader as RW
except Exception as exc:                                  # noqa: BLE001
    print("× 导入 shadow_warchess_reader 失败：%r" % (exc,))
    sys.exit(2)

try:
    import read_world as RWD
except Exception as exc:                                  # noqa: BLE001
    print("（read_world 导入失败，第 6 步跳过：%r）" % (exc,))
    RWD = None

LINE = "-" * 66
KEYS = ("shadowverse", "影之诗", "warchess", "mumu")
SKIP = ("launcher", "crashpad", "crash", "reporter", "unicrash",
        "handler", "service", "healthd", "backend", "plugin", "uninstall",
        "update", "setup", "helper")
MIN_WS = 200 * 1048576

psapi = RW.psapi
k32 = RW.k32

problems = []


def say(s=""):
    print(s, flush=True)


def head(t):
    say()
    say(LINE)
    say(t)
    say(LINE)


# ────────────────────────────────────────────── 1. 环境
def step_env():
    head("[1] 运行环境")
    say("Python      : %s" % sys.version.split()[0])
    say("解释器      : %s" % sys.executable)
    try:
        import numpy
        say("numpy       : %s" % numpy.__version__)
    except Exception as exc:                              # noqa: BLE001
        say("numpy       : × 缺失（%s）" % exc)
        problems.append("缺 numpy：pip install numpy")
    try:
        import PIL
        say("Pillow      : %s" % getattr(PIL, "__version__", "?"))
    except Exception as exc:                              # noqa: BLE001
        say("Pillow      : × 缺失（%s）" % exc)
        problems.append("缺 Pillow：pip install pillow")
    try:
        import frida
        say("frida       : %s" % frida.__version__)
        return frida
    except Exception as exc:                              # noqa: BLE001
        say("frida       : × 未安装（%s）" % exc)
        problems.append("缺 frida：pip install frida —— 不装就永远读不到记牌器数据")
        return None


# ────────────────────────────────────────────── 2. 进程
def step_procs():
    head("[2] 进程扫描（找「游戏」进程）")
    say("%-7s %-8s %-46s %s" % ("PID", "内存MB", "进程名", "判定"))
    hits = []
    for pid in RW.enum_pids():
        p = RW.pid_image(pid)
        if not p:
            continue
        base = os.path.basename(p).lower()
        ws = RW._workingset(pid)
        named = any(k in base for k in KEYS) or "影之诗" in p
        # 只印「名字像游戏」或「内存够大」的，免得刷屏
        if not named and ws < MIN_WS:
            continue
        why = []
        if any(s in base for s in SKIP):
            why.append("名字含辅助进程关键字")
        if not ("shadowverse" in base or "影之诗" in p or "warchess" in base
                or "mumu" in base):
            why.append("名字不含 shadowverse/影之诗/warchess/mumu")
        if ws < MIN_WS:
            why.append("内存不足 200MB")
        ok = not why
        if ok:
            hits.append((ws, pid, p))
        say("%-7d %-8d %-46s %s" % (pid, ws // 1048576,
                                    os.path.basename(p)[:44],
                                    "✓ 通过" if ok else "× " + "，".join(why)))
    if not hits:
        say()
        say("× 没有任何进程通过过滤 —— 助手会一直认为「游戏没启动」。")
        problems.append("进程过滤没命中：游戏没开，或进程名不在白名单里")
    return hits


# ────────────────────────────────────────────── 3. 权限
def step_open(pid_guess):
    head("[3] 打开进程 / 读内存权限")
    pid, path = RW.find_game_pid()
    say("find_game_pid() → %s" % (("%d  %s" % (pid, path)) if pid else "None"))
    if not pid:
        problems.append("find_game_pid 没结果（见第 2 步）")
        return None, None
    h = k32.OpenProcess(0x0400 | 0x0010, False, pid)   # QUERY_INFORMATION|VM_READ
    if not h:
        err = C.get_last_error()
        reason = {5: "拒绝访问 → 权限不一致，请用管理员身份运行 启动.bat",
                  87: "参数错误", 1168: "进程不存在"}.get(err, "未知")
        say("× OpenProcess 失败 err=%d （%s）" % (err, reason))
        if err == 5:
            problems.append("OpenProcess 被拒（err=5）：助手和游戏权限不一致，必须同为管理员")
        return None, None
    say("OpenProcess 成功，句柄=%s" % h)
    regs = RW.regions(h)
    total = sum(sz for _b, sz in regs)
    say("可读内存区域 %d 个，共 %.1f GB" % (len(regs), total / 1073741824.0))
    if not regs:
        say("× 一个可读区域都没有 —— 进程可能被保护（反调试/加密内存）")
        problems.append("目标进程没有可读区域（可能被保护）")
    else:
        ok = 0
        for b, sz in regs[:5]:
            if RW.read(h, b, 64):
                ok += 1
        say("抽样读取 %d/5 成功" % ok)
        if ok == 0:
            say("× 能打开但读不到内容 → 强保护或权限不足")
            problems.append("能打开进程但 ReadProcessMemory 全部失败")
    cards = RW.load_cards()
    say("本地卡表 %d 张" % len(cards))
    return h, cards


# ────────────────────────────────────────────── 4. 模块
def step_modules(h):
    head("[4] 目标进程加载的模块（记牌器依赖 GameAssembly.dll）")
    if not h:
        say("（跳过）")
        return
    LIST_MODULES_ALL = 0x03
    arr = (W.HMODULE * 2048)()
    need = W.DWORD(0)
    psapi.EnumProcessModulesEx.argtypes = [W.HANDLE, C.POINTER(W.HMODULE),
                                           W.DWORD, C.POINTER(W.DWORD), W.DWORD]
    psapi.EnumProcessModulesEx.restype = W.BOOL
    if not psapi.EnumProcessModulesEx(h, arr, C.sizeof(arr), C.byref(need),
                                      LIST_MODULES_ALL):
        say("× EnumProcessModulesEx 失败 err=%d（权限不足？）" % C.get_last_error())
        return
    names = []
    for i in range(min(need.value // C.sizeof(W.HMODULE), 2048)):
        buf = C.create_unicode_buffer(260)
        psapi.GetModuleBaseNameW.argtypes = [W.HANDLE, W.HMODULE, W.LPWSTR, W.DWORD]
        if psapi.GetModuleBaseNameW(h, arr[i], buf, 260):
            names.append((buf.value, arr[i]))
    say("共 %d 个模块" % len(names))
    want = ("gameassembly.dll", "unityplayer.dll", "il2cpp", "msvcr", "mono")
    for n, base in names:
        low = n.lower()
        if any(w in low for w in want):
            say("  %-32s base=0x%X" % (n, base))
    ga = [n for n, _b in names if n.lower() == "gameassembly.dll"]
    if not ga:
        say()
        say("× 没有 GameAssembly.dll —— 记牌器（frida hook）一定失败：")
        say("  frida_live.py 里 Process.findModuleByName('GameAssembly.dll') 会拿到 null。")
        problems.append("目标进程没有 GameAssembly.dll（frida_live 无法 hook）")


# ────────────────────────────────────────────── 5. il2cpp 类
def step_classes(h):
    head("[5] il2cpp 类定位（类名在内存里找得到吗）")
    if not h:
        say("（跳过）")
        return {}
    targets = [
        ("Sv2WarchessWorldData", "Sv2Warchess"),
        ("Sv2WarchessBattlePlayerBase", "Sv2Warchess"),
        ("Sv2WarchessBattleCardBase", "Sv2Warchess"),
        ("S_COMBAT_OPPONENT", "Netease.GameNet.MOS_PROTO"),
    ]
    got = {}
    for name, ns in targets:
        t0 = time.time()
        try:
            r = RW.find_class(h, name, ns)
        except Exception as exc:                          # noqa: BLE001
            say("  %-30s 异常 %r" % (name, exc))
            continue
        cls = r[0] if isinstance(r, tuple) else r
        dt = time.time() - t0
        got[name] = cls
        if cls:
            say("  %-30s ✓ 0x%X   (%.1fs)" % (name, cls, dt))
        else:
            say("  %-30s × 未找到  (%.1fs)" % (name, dt))
    if not got.get("Sv2WarchessWorldData"):
        say()
        say("× 世界数据类找不到。三种可能：")
        say("  a) 当时不在对局里（大厅/结算界面没有这个类的实例，但类本身一般还在）")
        say("  b) 游戏更新后类名/命名空间变了 → 工具的类名字符串要跟着改")
        say("  c) global-metadata 被加密，内存里没有明文字符串")
        problems.append("Sv2WarchessWorldData 类未找到")
    return got


# ────────────────────────────────────────────── 6. 实例与字段
def step_snapshot(h, cards, classes):
    head("[6] 实例扫描 + 玩家字段合理性")
    if not h or not RWD:
        say("（跳过）")
        return
    wd = classes.get("Sv2WarchessWorldData")
    if wd:
        t0 = time.time()
        inst = RW.scan_qwords(h, {wd})
        say("WorldData 实例指针出现 %d 处（%.1fs）" % (len(inst), time.time() - t0))
        for a in inst[:5]:
            say("   ref @0x%X" % a)
    try:
        RWD.WD_PID[0] = RW.find_game_pid()[0]
        t0 = time.time()
        snap = RWD.snapshot(h, cards, force_cold=True)
        say("snapshot() 用时 %.1fs，返回键：%s" % (time.time() - t0,
                                                 ", ".join(snap.keys())))
        err = snap.get("error")
        if err:
            say("× snapshot 报错：%s" % err)
            problems.append("snapshot 报错：%s" % err)
        players = snap.get("players") or []
        say("读到玩家对象 %d 个" % len(players))
        for p in players:
            say("   %s uid=%s hero=%s life=%s grade=%s evo=%s 随从=%d"
                % (p.get("side"), p.get("uid"), p.get("hero"), p.get("life"),
                   p.get("grade"), p.get("evolve"), len(p.get("minions") or [])))
            life, grade = p.get("life"), p.get("grade")
            if life is not None and not (-5 <= life <= 200):
                problems.append("血量 %s 明显不合理 → 字段偏移错位" % life)
            if grade is not None and not (0 <= grade <= 10):
                problems.append("幻境等级 %s 不合理 → 字段偏移错位" % grade)
        if not players and not err:
            say("（没有玩家对象：此刻不在战斗/运营阶段，属正常；"
                "请进一局「影之幻境」再跑一次本脚本）")
    except Exception as exc:                              # noqa: BLE001
        say("× snapshot 异常：%r" % (exc,))
        problems.append("snapshot 异常：%r" % (exc,))


# ────────────────────────────────────────────── 7. frida
def step_frida(frida, pid, do_attach):
    head("[7] 记牌器数据源（Frida）")
    if frida is None:
        say("× frida 没装，第 7 步无法进行 —— 这就是记牌器一直是「未在对战中」的原因。")
        return
    say("frida %s 已就绪" % frida.__version__)
    if not do_attach:
        say("（加 --frida 参数可测试能否 attach）")
        return
    if not pid:
        say("（没有目标进程，跳过 attach）")
        return
    try:
        t0 = time.time()
        s = frida.attach(int(pid))
        say("attach 成功（%.2fs），进程 %d" % (time.time() - t0, pid))
        s.detach()
        say("detach 完成")
    except Exception as exc:                              # noqa: BLE001
        say("× attach 失败：%r" % (exc,))
        problems.append("frida attach 失败：%r" % (exc,))


def main():
    frida = step_env()
    hits = step_procs()
    h, cards = step_open(hits[0][1] if hits else None)
    step_modules(h)
    classes = step_classes(h)
    step_snapshot(h, cards, classes)
    pid = RW.find_game_pid()[0]
    step_frida(frida, pid, "--frida" in sys.argv)

    head("结论")
    if problems:
        for i, p in enumerate(problems, 1):
            say("%d. %s" % (i, p))
    else:
        say("没发现明显问题。如果还是读不到，请在「战斗阶段」再跑一次本脚本。")
    say()
    say("本脚本全程只读，没有修改游戏或任何设置。")


if __name__ == "__main__":
    main()

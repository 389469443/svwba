"""影之诗幻境「对局双方实时状态」读取器 —— 只读内存，多源交叉验证

一条命令（默认参数即可）：
    python shadow_warchess_reader.py

依次尝试 5 个数据源，逐个打印「类是否找到 / 引用数 / 实例地址 / 读出的值」，
并把每个候选实例 0x10~0x200 的原始内存 dump 到 dump_<时间戳>.txt（控制台只印摘要）。

数据源（按稳定性排序）
  1. Sv2WarchessBattlePlayerBase   —— 战斗中恒有 2 个实例（我方+对方），最稳
  2. Sv2Warchess.TavernOwnerData   —— 我方幻境等级 CurGrade
  3. S_TAVERN_PLAYER_INFO          —— 全桌每个玩家的 cur_grade
  4. S_COMBAT_SETTLEMENT           —— 服务端直发的真实扣血 damage（校验公式用）
  5. S_COMBAT_OPPONENT             —— 战斗开场瞬时 DTO（对方 grade + 双方 minions）

其他模式
    --probe            每 2 秒打印各源命中数（看"什么时候有实例"）
    --watch 2          完整快照每 2 秒刷新
    --json             机器可读输出
    --out FILE         指定 dump 文件路径

安全性：只 OpenProcess(QUERY_INFORMATION|VM_READ) + ReadProcessMemory。
无写入、无注入、无 hook、无驱动；不改游戏文件、不改注册表。
"""
import argparse
import ctypes as C
import ctypes.wintypes as W
import json
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
CARDS_JSON = os.path.join(HERE, "..", "dsh-shadowverse-huanjing", "lib", "knowledge", "cards.json")

k32 = C.WinDLL("kernel32", use_last_error=True)
psapi = C.WinDLL("psapi", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
MEM_PRIVATE = 0x20000

k32.OpenProcess.restype = W.HANDLE
k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k32.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
k32.ReadProcessMemory.restype = W.BOOL


class MEMORY_BASIC_INFORMATION(C.Structure):
    _fields_ = [("BaseAddress", C.c_void_p), ("AllocationBase", C.c_void_p),
                ("AllocationProtect", W.DWORD), ("RegionSize", C.c_size_t),
                ("State", W.DWORD), ("Protect", W.DWORD), ("Type", W.DWORD)]


k32.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MEMORY_BASIC_INFORMATION), C.c_size_t]
k32.VirtualQueryEx.restype = C.c_size_t


# ------------------------------------------------------------------ process
def enum_pids():
    arr = (W.DWORD * 8192)()
    need = W.DWORD(0)
    psapi.EnumProcesses.argtypes = [C.POINTER(W.DWORD), W.DWORD, C.POINTER(W.DWORD)]
    psapi.EnumProcesses.restype = W.BOOL
    if not psapi.EnumProcesses(arr, C.sizeof(arr), C.byref(need)):
        return []
    return list(arr[:need.value // 4])


def pid_image(pid):
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return None
    buf = C.create_unicode_buffer(1024)
    size = W.DWORD(1024)
    k32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
    ok = k32.QueryFullProcessImageNameW(h, 0, buf, C.byref(size))
    k32.CloseHandle(h)
    return buf.value if ok else None


def _workingset(pid):
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return 0

    class PMC(C.Structure):
        _fields_ = [("cb", W.DWORD), ("PageFaultCount", W.DWORD),
                    ("PeakWorkingSetSize", C.c_size_t), ("WorkingSetSize", C.c_size_t),
                    ("QuotaPeakPagedPoolUsage", C.c_size_t), ("QuotaPagedPoolUsage", C.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", C.c_size_t), ("QuotaNonPagedPoolUsage", C.c_size_t),
                    ("PagefileUsage", C.c_size_t), ("PeakPagefileUsage", C.c_size_t)]

    x = PMC(); x.cb = C.sizeof(PMC)
    psapi.GetProcessMemoryInfo.argtypes = [W.HANDLE, C.c_void_p, W.DWORD]
    ws = x.WorkingSetSize if psapi.GetProcessMemoryInfo(h, C.byref(x), C.sizeof(PMC)) else 0
    k32.CloseHandle(h)
    return ws


class _FT(C.Structure):
    _fields_ = [("lo", W.DWORD), ("hi", W.DWORD)]


def pid_age(pid):
    """进程已经启动了多久（秒）；拿不到返回 None。

    ⚠️ 为什么需要它（2026-11-07 用户实测）：关掉游戏后，模拟器容器会立刻
       以**新的 pid** 重新起来，`find_game_pid()` 一眼就能找到它。控制台看到
       「换了个 pid」就当成「玩家重新开了游戏」，马上去 attach ——
       可那个进程还在初始化界面/正在收尾，Frida 一挂上去就把它的主线程钉住，
       用户看到的就是「游戏关不掉、未响应」。

       所以挂之前必须先看年龄：刚出生十几秒的进程一律不碰，等它稳定下来。
    """
    h = k32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        c, e, k, u = _FT(), _FT(), _FT(), _FT()
        k32.GetProcessTimes.argtypes = [W.HANDLE, C.POINTER(_FT),
                                        C.POINTER(_FT), C.POINTER(_FT),
                                        C.POINTER(_FT)]
        if not k32.GetProcessTimes(h, C.byref(c), C.byref(e),
                                   C.byref(k), C.byref(u)):
            return None
        born = ((c.hi << 32) | c.lo) / 1e7            # 100ns → 秒（1601 起）
        now = time.time() + 11644473600.0             # Unix → 1601 起
        age = now - born
        return age if age >= 0 else 0.0
    except Exception:
        return None
    finally:
        try:
            k32.CloseHandle(h)
        except Exception:
            pass


# ------------------------------------------------------------ 进程名特征
# ⚠️ 2026-11-13：朋友的 MuMu「非高清版」认不到游戏，根子就在这串关键词太窄。
#    以前只认 shadowverse / 影之诗 / warchess / mumu，而且 mumu **只比文件名**。
#    可模拟器一换代，宿主上的进程就叫 NemuHeadless.exe、MuMuVMMHeadless.exe、
#    MuMuNxDevice.exe …… 有的连 "mumu" 都没有（网易系叫 Nemu*），
#    有的关键词只出现在**路径**里（C:\Program Files\Netease\MuMu\...），
#    于是一个都命中不了 —— 表现就是界面永远「读取未启动」。
#    现在：文件名和完整路径一起比，关键词覆盖网易系 / 雷神系 / 海外系常见写法。
# 硬关键词：命中就是模拟器/游戏，不用再犹豫
STRONG_KEYS = ("shadowverse", "warchess", "影之诗", "mumu", "nemu",
               "nexus", "nxdevice", "nxmain", "ldplayer", "dnplayer",
               "bluestacks", "bstk", "memu")
# 软关键词：网易/虚拟机的通用词。网易云音乐、UU 加速器之类也在这些目录下，
# 所以软命中**额外要求工作集 ≥ 800MB**（真模拟器都是 GB 级，音乐客户端到不了）
WEAK_KEYS = ("netease", "vbox")
GAME_KEYS = STRONG_KEYS + WEAK_KEYS
WEAK_MIN_WS = 800 * 1048576
# 辅助进程：名字命中了也不是游戏本体（崩溃报告、更新器、服务、卸载器……）
SKIP = ("launcher", "crashpad", "crash", "reporter", "unicrash",
        "handler", "service", "healthd", "backend", "plugin", "uninstall",
        "update", "setup", "helper", "installer", "advertise", "updater")
# 宿主上的解释器：助手自己的路径里就带「影之诗」，不加这条会把自己当成游戏
SELF_SKIP = ("python.exe", "pythonw.exe", "javaw.exe", "node.exe",
             "cmd.exe", "conhost.exe", "wt.exe", "code.exe")
MIN_WS = 200 * 1048576          # 200 MB
_DIAG_AT = 0.0


def _verdict(p, base):
    """一个进程该怎么看：(是不是候选, 原因)。诊断日志和选进程共用这一套判断。"""
    if any(s in base for s in SKIP):
        return False, "辅助进程"
    if base in SELF_SKIP:
        return False, "解释器"
    low = p.lower()
    # 助手自己就装在「影之诗助手」目录里，路径会被 "影之诗" 命中 —— 先排掉
    if "影之诗助手" in low:
        return False, "助手自己"
    # 文件名优先（更准），文件名不中再看完整路径
    strong = [k for k in STRONG_KEYS if k in base] or \
             [k for k in STRONG_KEYS if k in low]
    if strong:
        return True, "命中%s" % strong[0]
    weak = [k for k in WEAK_KEYS if k in base] or \
           [k for k in WEAK_KEYS if k in low]
    if weak:
        return True, "弱命中%s" % weak[0]
    return False, "名字不命中"


def _scan_candidates():
    """所有够格的候选（工作集够大的模拟器/游戏进程），按工作集降序。"""
    cands = []
    for pid in enum_pids():
        if not pid or pid == os.getpid():
            continue
        p = pid_image(pid)
        if not p:
            continue
        # ⚠️ 不要要求扩展名是 .exe：本机游戏的可执行文件实际叫
        #    "MuMu模拟器x影之诗高清版.o"（没有 .exe 后缀）。
        base = os.path.basename(p).lower()
        ok, why = _verdict(p, base)
        if not ok:
            continue
        ws = _workingset(pid)
        if ws < MIN_WS:
            continue
        # 软关键词（netease/vbox）可能撞上网易云音乐这类普通客户端，
        # 真游戏在模拟器里从没低于 800MB，用工作集把误判挡掉
        if why.startswith("弱命中") and ws < WEAK_MIN_WS:
            continue
        cands.append((ws, pid, p, why))
    cands.sort(reverse=True)
    return cands


def dump_process_diag(limit=40, result=None):
    """把「宿主上都有什么进程」写进 logs/进程识别.log。

    为什么需要它：朋友的机器上没装 Python、也不会跑诊断脚本，但**助手自己**
    在找进程 —— 让它把现场写下来，比让人装工具靠谱。只在「没找到游戏进程」
    时写，每次重写（不追加），不会越滚越大。
    """
    path = os.path.join(HERE, "logs", "进程识别.log")
    try:
        import datetime
        rows = []
        for pid in enum_pids():
            if not pid:
                continue
            p = pid_image(pid)
            if not p:
                continue
            rows.append((_workingset(pid), pid, p))
        rows.sort(reverse=True)
        lines = [
            "影之诗助手 · 进程识别（找不到游戏时自动写）",
            "时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "规则：硬关键词 %s；软关键词 %s（要 ≥%dMB）；排除 %s；工作集 ≥ %dMB" % (
                "/".join(STRONG_KEYS), "/".join(WEAK_KEYS),
                WEAK_MIN_WS // 1048576, "/".join(SKIP), MIN_WS // 1048576),
            "本次结果：%s" % (result or "无候选"),
            "",
            "%-8s %-9s %-12s %s" % ("PID", "工作集", "结论", "路径"),
        ]
        for ws, pid, p in rows[:limit]:
            ok, why = _verdict(p, os.path.basename(p).lower())
            lines.append("%-8d %8dMB %-12s %s"
                         % (pid, ws // 1048576, ("候选" if ok else why), p))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return path
    except Exception:
        return None


def find_game_pid():
    """
    找出**真正的游戏进程**。

    ⚠️ 这里踩过一个坑：原来的匹配用**完整路径**判断 "shadowverse"，
       而游戏装在 D:/Shadowverse/ 下，于是 D:/Shadowverse/UniCrashReporter.exe
       （崩溃报告器，只有几 MB）也被当成游戏，还被选中了 ——
       结果五个源全部"类未找到"，因为扫的是一个空进程。

    修正：
      1) 文件名和完整路径都参与匹配（见 GAME_KEYS）
      2) 排除 crash / reporter / unicrash 等辅助进程，也排除解释器（助手自己）
      3) 要求工作集 > 200MB（游戏实测 700MB+，辅助进程都远小于此）
      4) 多个候选时取工作集最大的
      5) 一个都没找到时，把现场写进 logs/进程识别.log（30 秒最多一次）
    """
    cands = _scan_candidates()
    if cands:
        ws, pid, p, why = cands[0]
        return pid, p
    global _DIAG_AT
    now = time.time()
    if now - _DIAG_AT > 30:      # 控制台会反复重试，别每次都写盘
        _DIAG_AT = now
        dump_process_diag()
    return None, None


# ------------------------------------------------------------------ memory
def open_proc(pid):
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        raise SystemExit(f"OpenProcess({pid}) 失败 err={C.get_last_error()}（权限不足或进程已退出）")
    return h


def regions(h, private_only=False):
    out, addr, mbi = [], 0, MEMORY_BASIC_INFORMATION()
    while k32.VirtualQueryEx(h, C.c_void_p(addr), C.byref(mbi), C.sizeof(mbi)):
        base, size = mbi.BaseAddress or 0, mbi.RegionSize or 0
        if size == 0:
            break
        if (mbi.State == MEM_COMMIT and not (mbi.Protect & PAGE_NOACCESS)
                and not (mbi.Protect & PAGE_GUARD)):
            if not private_only or mbi.Type == MEM_PRIVATE:
                out.append((base, size))
        addr = base + size
        if addr > 0x7FFFFFFFFFFF:
            break
    return out


def read(h, addr, size):
    buf = (C.c_char * size)()
    got = C.c_size_t(0)
    if k32.ReadProcessMemory(h, C.c_void_p(addr), buf, size, C.byref(got)) and got.value:
        return buf.raw[:got.value]
    return None


def scan_bytes(h, needle, limit=16, chunk=8 << 20):
    hits = []
    for base, size in regions(h):
        off = 0
        while off < size:
            n = min(chunk, size - off)
            d = read(h, base + off, n)
            if d:
                pos = 0
                while True:
                    i = d.find(needle, pos)
                    if i < 0:
                        break
                    hits.append(base + off + i)
                    pos = i + 1
                    if len(hits) >= limit:
                        return hits
            off += n
    return hits


def _scan_region_qwords(args):
    """扫描单个内存区域，返回命中地址列表（供线程池调用）"""
    h, base, size, tgt, chunk = args
    out = []
    off = 0
    while off < size:
        n = min(chunk, size - off)
        d = read(h, base + off, n)
        if d and len(d) >= 8:
            arr = np.frombuffer(d[:len(d) // 8 * 8], dtype="<u8")
            idx = np.isin(arr, tgt)
            if idx.any():
                for i in np.nonzero(idx)[0]:
                    out.append(base + off + int(i) * 8)
        off += n
    return out


def scan_qwords(h, targets, chunk=8 << 20, threads=8, private_only=False,
                region_list=None):
    """
    在进程内存里搜「值等于 targets 之一」的 8 字节位置。

    性能：6.4 GB 的进程内存，单线程约 12 秒；这里按**区域**切给线程池并行扫，
    8 线程实测能压到 2~3 秒（ReadProcessMemory 会释放 GIL，所以线程有效）。
    `region_list` 允许只扫指定的区域（例如"旧地址附近"），用于快速重定位。
    """
    tgt = np.array(sorted(targets), dtype=np.uint64)
    todo = region_list if region_list is not None else regions(h, private_only=private_only)
    if not todo:
        return []
    # 区域大小差异很大：按大小降序，避免最后只剩一个大区域拖尾
    todo = sorted(todo, key=lambda r: -r[1])
    found = []
    n_threads = max(1, int(threads))
    if n_threads == 1 or len(todo) == 1:
        for base, size in todo:
            found.extend(_scan_region_qwords((h, base, size, tgt, chunk)))
        return found
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        for part in ex.map(_scan_region_qwords,
                           [(h, b, sz, tgt, chunk) for b, sz in todo]):
            found.extend(part)
    return found


def scan_qwords_near(h, targets, hint, window=192 << 20, chunk=8 << 20,
                     threads=8):
    """
    只扫 `hint` 附近的内存（同一区域 ±window），用于**快速重定位**。

    为什么管用：新一局的世界对象通常和上一局在**同一个大堆段**里，
    优先扫这个窗口往往一击即中，避免全量扫 6 GB。
    返回 [] 表示附近没找到（调用方再退回全量扫描）。
    """
    try:
        hint = int(hint)
    except Exception:
        return []
    near = []
    for base, size in regions(h, private_only=True):
        if base + size <= hint - window or base >= hint + window:
            continue
        near.append((base, size))
    if not near:
        return []
    return scan_qwords(h, targets, chunk=chunk, threads=threads,
                       region_list=near)


def cstr(h, addr, maxlen=96):
    d = read(h, addr, maxlen)
    if not d:
        return None
    z = d.find(b"\x00")
    try:
        return d[:z if z >= 0 else len(d)].decode("ascii")
    except UnicodeDecodeError:
        return None


def find_class(h, tname, nsname):
    hits = scan_bytes(h, tname.encode() + b"\x00", limit=16)
    if not hits:
        return None
    for slot in scan_qwords(h, set(hits)):
        cand = slot - 0x10
        nsp = read(h, cand + 0x18, 8)
        if not nsp:
            continue
        s = cstr(h, struct.unpack("<Q", nsp)[0])
        if s and s.startswith(nsname):
            return cand
    return None


# ------------------------------------------------------------------ layouts
def layout_decl(fields, header=0x10):
    off, out = header, {}
    for name, size in fields:
        if off % size:
            off += size - off % size
        out[name] = off
        off += size
    return out


def layout_width(fields, header=0x10):
    off, out = header, {}
    for size in (8, 4, 2, 1):
        for name, sz in fields:
            if sz != size:
                continue
            if off % size:
                off += size - off % size
            out[name] = off
            off += size
    return out


def layout_slots(fields, header=0x10):
    off, out = header, {}
    for name, _sz in fields:
        out[name] = off
        off += 8
    return out


LAYOUTS = (("decl", layout_decl), ("width", layout_width), ("slots", layout_slots))
KIND_SIZE = {"i64": 8, "i32": 4, "i16": 2, "i8": 1, "ptr": 8}


def rd(h, addr, kind):
    sz = KIND_SIZE[kind]
    d = read(h, addr, sz)
    if not d or len(d) < sz:
        return None
    if kind == "i64":
        return struct.unpack("<q", d)[0]
    if kind == "i32":
        return struct.unpack("<i", d)[0]
    if kind == "i16":
        return struct.unpack("<h", d)[0]
    if kind == "i8":
        return struct.unpack("<b", d)[0]
    return struct.unpack("<Q", d)[0]


def kind_of(size):
    return {8: "i64", 4: "i32", 2: "i16", 1: "i8"}.get(size, "i32")


# ------------------------------------------------------------------ 数据源定义
# 字段顺序 = metadata 声明顺序；size 仅用于推偏移
BATTLE_PLAYER = [("UserId", 8), ("HeroId", 4), ("Life", 4), ("Armor", 4), ("Grade", 4),
                 ("EvolvePoint", 4), ("EvolveUnlocked", 4), ("SuperEvolveUnlocked", 4),
                 ("MaxEvolvePoint", 4), ("SuperEvolvePoint", 4), ("MaxSuperEvolvePoint", 4),
                 ("IsPriviliged", 1), ("IsShowHighBorder", 1), ("ShowBorderInfo", 4),
                 ("DoubleSkillId", 4), ("HandCardNum", 4), ("ShopCardList", 8),
                 ("HandCards", 8), ("BattleCards", 8), ("HeroSkillCardList", 8),
                 ("SkillCards", 8), ("SpecialCardList", 8), ("LocalEventMgr", 8)]

TAVERN_OWNER = [("MAX_GRADE", 4), ("CurGrade", 2), ("UpgradeCost", 2), ("BaseCost", 2),
                ("RefreshCost", 2), ("LockState", 2), ("Gold", 2), ("MaxGold", 2),
                ("BuyCost", 4), ("RefreshCnt", 2), ("IsRefreshCooldown", 1),
                ("SkillCostModifier", 8), ("OnTavernDataChanged", 8),
                ("_cooldownCts", 8), ("_refreshDelayStarted", 1)]

TAVERN_PLAYER_INFO = [("uid", 8), ("hero_id", 4), ("evolve_point", 2), ("cur_grade", 2),
                      ("three_evolve_cnt", 2), ("win_streak", 2), ("record", 8),
                      ("minions", 8), ("evolve_round", 2), ("selected_degree_id", 8),
                      ("latest_round", 4), ("selected_emblem_id", 8), ("is_priviliged", 2),
                      ("life", 8), ("double_skill_id", 4), ("double_skill_open_round", 4),
                      ("max_evolve_point", 2), ("super_evolve_point", 2),
                      ("max_super_evolve_point", 2), ("left_super_evolve_round", 2),
                      ("evolve_unlocked", 2), ("super_evolve_unlocked", 2),
                      ("used_evolve_point", 2), ("used_super_evolve_point", 2)]

COMBAT_SETTLEMENT = [("uid", 8), ("match_result", 2), ("damage", 2),
                     ("opponent_player", 8), ("self_player", 8), ("protect_hurt", 2)]

COMBAT_OPPONENT = [("uid", 8), ("hero_id", 4), ("life", 8), ("armor", 8), ("grade", 2),
                   ("random_num", 4), ("minions", 8), ("evolve_point", 4), ("cards_num", 4),
                   ("self_minions", 8), ("self_cards", 8), ("skills", 8), ("skill_cost", 4),
                   ("is_priviliged", 4), ("hero_boarderid", 4), ("hit_effect_id", 4),
                   ("kill_effect_id", 4), ("show_border", 4), ("double_skill_id", 4),
                   ("max_evolve_point", 2), ("evolve_unlocked", 2), ("super_evolve_point", 2),
                   ("max_super_evolve_point", 2), ("super_evolve_unlocked", 2),
                   ("can_use_hero_skill", 2)]

CARD_MINIONS = [("unit_id", 4), ("card_id", 4), ("atk", 8), ("life", 8),
                ("evolve_state", 2), ("buff_state", 2), ("skills", 8), ("buff_info", 8),
                ("locked", 2), ("left_skill_times", 2), ("buy_list_index", 4),
                ("stack_num", 2), ("green_effect", 4), ("damage_after_stop", 2),
                ("star", 2), ("cant_point_evolve", 2), ("skill_effect_list", 8),
                ("atk_count", 2), ("card_class_list", 8), ("has_select_skill", 2)]

SOURCES = [
    {"name": "BattlePlayerBase", "type": "Sv2WarchessBattlePlayerBase",
     "ns": "Sv2Warchess", "fields": BATTLE_PLAYER,
     "keys": {"uid": "UserId", "hero": "HeroId", "grade": "Grade", "life": "Life", "armor": "Armor"}},
    {"name": "TavernOwner", "type": "TavernOwnerData",
     "ns": "Sv2Warchess", "fields": TAVERN_OWNER,
     "keys": {"grade": "CurGrade", "gold": "Gold", "maxgold": "MaxGold"}},
    {"name": "TavernPlayerInfo", "type": "S_TAVERN_PLAYER_INFO",
     "ns": "Netease.GameNet.MOS_PROTO", "fields": TAVERN_PLAYER_INFO,
     "keys": {"uid": "uid", "grade": "cur_grade", "life": "life"}},
    {"name": "CombatSettlement", "type": "S_COMBAT_SETTLEMENT",
     "ns": "Netease.GameNet.MOS_PROTO", "fields": COMBAT_SETTLEMENT,
     "keys": {"uid": "uid", "damage": "damage"}},
    {"name": "CombatOpponent", "type": "S_COMBAT_OPPONENT",
     "ns": "Netease.GameNet.MOS_PROTO", "fields": COMBAT_OPPONENT,
     "keys": {"uid": "uid", "grade": "grade", "life": "life", "cards": "cards_num"}},
]


def plausible_uid(v):
    return v is not None and 10 ** 8 < v < 10 ** 13


def verify_fields(name, v):
    """强校验：所有关键字段必须同时落在合理区间，才算"可信实例"。
    宁可报告"未验证"，也不要把垃圾值当成对方等级。"""
    g = v.get("grade")
    if name == "BattlePlayerBase":
        return (plausible_uid(v.get("uid")) and v.get("hero") is not None and 1 <= v["hero"] <= 10000
                and v.get("life") is not None and 0 <= v["life"] <= 100
                and g is not None and 1 <= g <= 8)
    if name == "TavernOwner":
        return (g is not None and 1 <= g <= 8
                and v.get("gold") is not None and 0 <= v["gold"] <= 99
                and v.get("maxgold") is not None and 0 <= v["maxgold"] <= 99)
    if name == "TavernPlayerInfo":
        return (plausible_uid(v.get("uid")) and g is not None and 1 <= g <= 8
                and v.get("life") is not None and 0 <= v["life"] <= 100)
    if name == "CombatSettlement":
        d = v.get("damage")
        return plausible_uid(v.get("uid")) and d is not None and 0 <= d <= 100
    if name == "CombatOpponent":
        return (plausible_uid(v.get("uid")) and g is not None and 1 <= g <= 8
                and v.get("life") is not None and -20 <= v["life"] <= 120)
    return False


def score_candidate(h, addr, src, off):
    s, vals = 0, {}
    fmap = dict(src["fields"])
    for label, fname in src["keys"].items():
        v = rd(h, addr + off[fname], kind_of(fmap[fname]))
        vals[label] = v
        if label == "uid" and plausible_uid(v):
            s += 4
        elif label == "hero" and v is not None and 1 <= v <= 10000:
            s += 3
        elif label == "grade" and v is not None and 1 <= v <= 12:
            s += 3
        elif label in ("life", "armor") and v is not None and -20 <= v <= 150:
            s += 1
        elif label in ("cards", "gold", "maxgold", "damage") and v is not None and 0 <= v <= 99:
            s += 1
    head = read(h, addr, 0x60)
    if head and len(head) >= 0x60:
        qs = struct.unpack("<12Q", head)
        if any(plausible_uid(x) for x in qs):
            s += 2
        s += min(3, sum(1 for x in qs if 0 <= x < 64))
    return s, vals


def dump_candidate(fh, h, addr, score, lname, vals):
    fh.write(f"\n--- 候选 {hex(addr)}  score={score}  layout={lname}\n")
    fh.write("    布局推断: " + ", ".join(f"{k}={vals[k]}" for k in vals) + "\n")
    fh.write(f"    {'addr':>18} {'qword':>20} {'int64':>18} {'int32_lo':>12} {'int16_lo':>9}  ptr?\n")
    data = read(h, addr + 0x10, 0x1F0)
    if not data:
        fh.write("    （读取失败）\n")
        return
    for i in range(0, len(data) - 7, 8):
        q = struct.unpack_from("<Q", data, i)[0]
        i64 = struct.unpack_from("<q", data, i)[0]
        i32 = struct.unpack_from("<i", data, i)[0]
        i16 = struct.unpack_from("<h", data, i)[0]
        ptr = "heap" if 0x10000 < q < 0x7FFFFFFFFFFF else ""
        fh.write(f"    {addr+0x10+i:016X} {q:20d} {i64:18d} {i32:12d} {i16:9d}  {ptr}\n")


def analyze_source(h, src, fh):
    res = {"name": src["name"], "type": f"{src['ns']}.{src['type']}"}
    cb = find_class(h, src["type"], src["ns"])
    if not cb:
        res["error"] = "类未找到（不在对局 / 类名被改）"
        fh.write(f"\n{'='*96}\n源 {res['name']} ({res['type']}): 类未找到\n")
        return res
    res["class"] = hex(cb)
    sites = scan_qwords(h, {cb})
    res["sites"] = len(sites)
    scored = []
    for addr in sites:
        best = None
        for lname, fn in LAYOUTS:
            off = fn(src["fields"])
            s, vals = score_candidate(h, addr, src, off)
            if best is None or s > best[0]:
                best = (s, lname, vals, off)
        scored.append((best[0], addr, best[1], best[2], best[3]))
    scored.sort(key=lambda x: -x[0])
    res["best_score"] = scored[0][0] if scored else 0
    res["instances"] = [{"addr": hex(a), "score": s, "layout": l,
                         "verified": verify_fields(src["name"], v), **v}
                        for s, a, l, v, _ in scored[:6]]
    res["verified_n"] = sum(1 for i in res["instances"] if i["verified"])
    fh.write(f"\n{'='*96}\n源 {res['name']} ({res['type']})  class={hex(cb)}  引用数={len(sites)}  "
             f"best_score={res['best_score']}\n{'='*96}\n")
    if not sites:
        fh.write("（没有任何指向该 class 的引用：此类型当前不存在实例）\n")
    for s, a, l, v, off in scored[:5]:
        dump_candidate(fh, h, a, s, l, v)
    return res


# ------------------------------------------------------------------ 卡牌
def load_cards():
    try:
        with open(os.path.abspath(CARDS_JSON), encoding="utf-8") as f:
            return json.load(f)["cards"]
    except Exception:
        return {}


def card_name(cards, cid):
    if not cid:
        return "?"
    for c in cards.values():
        if cid == c.get("baseCardId") or cid in (c.get("cardIds") or []):
            return c.get("name", "?")
    return "?"


def pick_card_offset(h, objs, cards):
    idset = set()
    for c in cards.values():
        idset.update(c.get("cardIds") or [])
        if c.get("baseCardId"):
            idset.add(c["baseCardId"])
    if not idset or not objs:
        return None, 0
    best = (0, None)
    for off in range(0x10, 0xA0, 4):
        hits = sum(1 for o in objs if rd(h, o + off, "i32") in idset)
        if hits > best[0]:
            best = (hits, off)
    return best[1], best[0]


def read_array(h, arr_ptr, limit=12):
    if not arr_ptr or arr_ptr < 0x10000:
        return []
    head = read(h, arr_ptr + 0x18, 8)
    if not head:
        return []
    n = struct.unpack("<Q", head)[0]
    if not (0 <= n <= 64):
        return []
    out = []
    for i in range(min(n, limit)):
        d = read(h, arr_ptr + 0x20 + i * 8, 8)
        if not d:
            break
        out.append(struct.unpack("<Q", d)[0])
    return out


def dump_minions(h, cards, arr_ptr):
    objs = [o for o in read_array(h, arr_ptr) if o > 0x10000]
    if not objs:
        return []
    cid_off, hits = pick_card_offset(h, objs, cards)
    off = layout_decl(CARD_MINIONS)
    if cid_off is not None:
        delta = cid_off - off["card_id"]
        off = {k: v + delta for k, v in off.items()}
    rows = []
    for o in objs:
        cid = rd(h, o + off["card_id"], "i32")
        rows.append({"obj": hex(o), "card_id": cid, "name": card_name(cards, cid),
                     "star": rd(h, o + off["star"], "i16"),
                     "atk": rd(h, o + off["atk"], "i64"),
                     "life": rd(h, o + off["life"], "i64")})
    return rows


# ------------------------------------------------------------------ 主流程
def run_all(h, cards, dump_path=None, quiet=False):
    dump_path = dump_path or os.path.join(HERE, time.strftime("dump_%Y%m%d-%H%M%S.txt"))
    results = []
    with open(dump_path, "w", encoding="utf-8-sig") as fh:
        fh.write(f"# 影之诗幻境 只读内存 dump  {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write("# 每行: 地址 / qword / int64 / 低4字节当int32 / 低2字节当int16\n")
        for src in SOURCES:
            try:
                results.append((src, analyze_source(h, src, fh)))
            except Exception as e:
                results.append((src, {"name": src["name"], "type": src["type"],
                                      "error": f"{type(e).__name__}: {e}"}))

    lines = ["=" * 78]
    for src, r in results:
        lines.append(f"[{r['name']:<17}] {r['type']}")
        if "error" in r:
            lines.append(f"    ✗ {r['error']}")
            continue
        inst = r.get("instances") or []
        ok = r.get("verified_n", 0) > 0
        lines.append(f"    类={r['class']}  引用={r['sites']}  best_score={r['best_score']}  "
                     f"{'✅ 有可信实例(字段校验通过)' if ok else '⚠ 无可信实例(布局未验证/不在对局)'}")
        for i in inst[:4]:
            kv = " ".join(f"{k}={i[k]}" for k in src["keys"] if k in i)
            mark = "✔" if i.get("verified") else " "
            lines.append(f"      {mark} {i['addr']} score={i['score']:2d} layout={i['layout']:<5} {kv}")

    for src, r in results:
        if src["name"] != "CombatOpponent" or r.get("verified_n", 0) <= 0:
            continue
        best = next(i for i in r["instances"] if i["verified"])
        addr = int(best["addr"], 16)
        off = dict(LAYOUTS)[best["layout"]](src["fields"])
        rows_all = []
        for tag, key in (("对方场上", "minions"), ("我方场上", "self_minions")):
            rows = dump_minions(h, cards, rd(h, addr + off[key], "ptr"))
            rows_all.extend(rows)
            lines.append(f"  {tag} ({len(rows)}):")
            for m in rows:
                lines.append(f"    {m['card_id']} {m['name']} {m['star']}★ {m['atk']}/{m['life']}")
        hits = sum(1 for m in rows_all if m["name"] != "?")
        lines.append(f"  卡表命中 {hits}/{len(rows_all)} 张"
                     + ("  ✅ 随从数据可信" if rows_all and hits == len(rows_all) else "  ⚠ 请对照截图"))

    lines.append("=" * 78)
    text = "\n".join(lines)
    if not quiet:
        print(text)
        n = sum(1 for _ in open(dump_path, encoding="utf-8"))
        print(f"\n完整内存 dump 已写入 {dump_path}（{n} 行）")
        print("若上面全是『无可信实例』，说明当前不在对局/不在战斗阶段，先开一局再跑。")
    return [r for _, r in results], dump_path, text


def probe(h):
    print("探针模式：每 2 秒打印各源引用数，Ctrl+C 结束\n")
    classes = {}
    while True:
        row = []
        for src in SOURCES:
            if src["type"] not in classes:
                classes[src["type"]] = find_class(h, src["type"], src["ns"])
            cb = classes[src["type"]]
            n = len(scan_qwords(h, {cb})) if cb else 0
            row.append(f"{src['name']}={n}")
        print(time.strftime("%H:%M:%S"), "  ".join(row), flush=True)
        time.sleep(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--watch", type=float, default=0)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()

    pid = args.pid
    if not pid:
        pid, _ = find_game_pid()
        if not pid:
            print("找不到游戏进程。请先启动《影之诗》，再运行本脚本。")
            return 1
    h = open_proc(pid)
    print(f"# pid={pid}  只读模式（ReadProcessMemory，无写入/无注入）\n")

    if args.probe:
        probe(h)
        return 0

    cards = load_cards()
    path = args.out
    while True:
        results, path, _ = run_all(h, cards, path, quiet=args.json)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=1))
        if not args.watch:
            break
        print(f"\n--- {time.strftime('%H:%M:%S')} 刷新 ---", flush=True)
        time.sleep(args.watch)
    return 0


if __name__ == "__main__":
    sys.exit(main())

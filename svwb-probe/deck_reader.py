#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗 · 记牌器读取层（2026-10-21，纯内存版）

数据来源（Frida 实测 + dump.cs）：
  BattleStatePlayerMpo
    HandCards          +16   BattleHandCardMpo[]（CardId +0x14）
    DeckCount          +32   int32
    Life               +36   int32
    PlayedCardIds      +112  int64[]（低 32 位 = 卡 id）
    DestroyedCardIds   +120  int64[]
  完整卡组：内存里 max_length 30~45、元素都是卡表 id 的数组。

全部只读（ReadProcessMemory），不注入、不 hook、不碰 GC。
"""
import json
import os
import threading
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

NAMES_PATH = os.path.join(HERE, "gamedata", "sv_card_names.json")
META_PATH = os.path.join(HERE, "gamedata", "sv_card_meta.json")
# dsh 解包卡表作为备用来源；它只有部分卡，但能覆盖助手卡表缺失的条目。
DSH_CARDS_PATH = os.path.join(os.path.dirname(HERE),
                              "dsh-shadowverse-huanjing", "lib", "knowledge", "cards.json")
USER_DECK_PATH = os.path.join(HERE, "gamedata", "user_deck.json")
# 测试/排查时指到临时副本，避免和常驻读取抢同一个文件
LIVE_PATH = os.environ.get("SVWB_LIVE") or os.path.join(
    HERE, "logs", "deck_live.json")
_NAMES = [None]
_META = [None]
_META_KEY = [None]
_META_LOCK = threading.RLock()
_DSH_CARDS = [None]

MIN_PTR = 0x10000
MAX_PTR = 0x7FFFFFFFFFFF

# il2cpp 运行时布局（64 位）
LIST_ITEMS_OFF = 0x10
LIST_SIZE_OFF = 0x18
ARRAY_DATA_OFF = 0x20

# 上次扫描到的地址（对局内有效，切局/地址失效时重扫）
_CACHE = {"state": 0, "deck": 0, "deck_ids": [], "at": 0.0}
_DECK_FILE_CACHE = {"key": None, "decks": [], "active": ""}
_DECK_FILE_LOCK = threading.RLock()


def load_names():
    if _NAMES[0] is None:
        try:
            with open(NAMES_PATH, encoding="utf-8") as fh:
                _NAMES[0] = json.load(fh)
        except Exception:
            _NAMES[0] = {}
    return _NAMES[0]


def load_meta():
    """卡牌元数据按文件修改时间缓存，卡组不变时不重复读完整卡表。"""
    try:
        st = os.stat(META_PATH)
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    with _META_LOCK:
        if _META[0] is not None and _META_KEY[0] == key:
            return _META[0]
        try:
            with open(META_PATH, encoding="utf-8") as fh:
                _META[0] = json.load(fh)
        except Exception:
            if _META[0] is None:
                _META[0] = {}
        _META_KEY[0] = key
        return _META[0]


def _deck_from_file():
    """手动导入的卡组：gamedata/user_deck.json 的 cards 数组（40 张，可重复）。

    截图/手填的兜底——内存扫描不到卡组时用它当基数。
    """
    try:
        with open(USER_DECK_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        return [int(c) for c in (d.get("cards") or []) if c]
    except Exception:
        return []


def _deck_file_key():
    paths = (os.path.join(HERE, "gamedata", "user_decks.json"), USER_DECK_PATH)
    out = []
    for path in paths:
        try:
            st = os.stat(path)
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _refresh_deck_file_cache():
    """卡组文件未变化时不重复读取和解析 JSON。"""
    key = _deck_file_key()
    with _DECK_FILE_LOCK:
        if key == _DECK_FILE_CACHE["key"]:
            return
        decks, active = [], ""
        p = os.path.join(HERE, "gamedata", "user_decks.json")
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
            active = d.get("active") or ""
            for item in d.get("decks") or []:
                cards = [int(c) for c in (item.get("cards") or []) if c]
                if cards:
                    decks.append({"name": item.get("name") or "", "cards": cards})
        except Exception:
            pass
        if not decks:
            cards = _deck_from_file()
            if cards:
                decks = [{"name": "", "cards": cards}]
        _DECK_FILE_CACHE.update(key=key, decks=decks, active=active)


def _decks_from_file():
    """读取缓存的手动卡组；文件变化时自动刷新。"""
    _refresh_deck_file_cache()
    with _DECK_FILE_LOCK:
        return list(_DECK_FILE_CACHE["decks"])


def _active_name():
    """从卡组文件缓存读取当前卡组名。"""
    _refresh_deck_file_cache()
    with _DECK_FILE_LOCK:
        return _DECK_FILE_CACHE["active"]


def _load_dsh_cards():
    if _DSH_CARDS[0] is None:
        try:
            with open(DSH_CARDS_PATH, encoding="utf-8") as fh:
                d = json.load(fh)
            _DSH_CARDS[0] = d.get("cards") or {}
        except Exception:
            _DSH_CARDS[0] = {}
    return _DSH_CARDS[0]


def _card_keys(cid):
    """生成同一张卡的可能 ID：原 ID、去掉异画尾号、dsh baseCardId。"""
    try:
        n = int(cid)
    except (TypeError, ValueError):
        return []
    out = [str(n)]
    # Shadowverse 的异画/形态 ID 常只在末位不同；仅作名称兜底，不改变计数。
    if n >= 10000000:
        out.extend([str(n - n % 10), str(n - n % 100)])
    return list(dict.fromkeys(out))


def card_meta(cid):
    """统一卡牌元数据查询，支持助手卡表和 dsh 解包卡表。"""
    meta = load_meta()
    for key in _card_keys(cid):
        v = meta.get(key)
        if isinstance(v, dict) and v.get("name"):
            return v
    dsh = _load_dsh_cards()
    for key in _card_keys(cid):
        v = dsh.get(key)
        if isinstance(v, dict) and v.get("name"):
            # 统一字段名，展示层和排序逻辑可以共用。
            return {"name": v.get("name"), "cost": v.get("cost"),
                    "rarity": v.get("rarity"), "atk": v.get("attack"),
                    "life": v.get("health"), "type": v.get("type"),
                    "class": v.get("class", v.get("classId"))}
    return {}


def card_name(cid):
    """卡 ID → 卡名；查不到时返回空字符串。"""
    v = card_meta(cid)
    return str(v.get("name") or "").strip()


def _name_of(cid):
    """卡 id → 卡名（用于把不同版本的同一张卡归并）。"""
    return card_name(cid)


# ---------------------------------------------------------------- 基础读取
def _u64(raw, off):
    return struct.unpack_from("<Q", raw, off)[0] if len(raw) >= off + 8 else 0


def _i32(raw, off):
    return struct.unpack_from("<i", raw, off)[0] if len(raw) >= off + 4 else 0


def _valid(p):
    return MIN_PTR < p < MAX_PTR


def read_list(mem, list_obj, max_n=200):
    """读 il2cpp List<T> 元素指针（保留给自测/旧接口）。"""
    if not list_obj:
        return []
    try:
        size = _i32(mem.read(list_obj + LIST_SIZE_OFF, 4), 0)
        if not (0 <= size <= max_n):
            return []
        items = _u64(mem.read(list_obj + LIST_ITEMS_OFF, 8), 0)
        if not _valid(items):
            return []
        out = []
        for i in range(size):
            p = _u64(mem.read(items + ARRAY_DATA_OFF + i * 8, 8), 0)
            if p:
                out.append(p)
        return out
    except Exception:
        return []


def read_card_ids(mem, list_obj, param_off=None, card_id_off=None):
    if param_off is None or card_id_off is None:
        return []
    out = []
    for obj in read_list(mem, list_obj):
        try:
            param = _u64(mem.read(obj + param_off, 8), 0)
            if not _valid(param):
                continue
            cid = _i32(mem.read(param + card_id_off, 4), 0)
            if 10000000 <= cid <= 99999999:
                out.append(cid)
        except Exception:
            continue
    return out


def diff_counts(total_ids, remaining_ids):
    from collections import Counter
    t = Counter(total_ids)
    r = Counter(remaining_ids)
    drawn = {}
    for cid, n in t.items():
        d = n - r.get(cid, 0)
        if d > 0:
            drawn[cid] = d
    return drawn, dict(t)


def summarize(total_ids, remaining_ids, hand_ids=()):
    from collections import Counter
    t = Counter(total_ids)
    r = Counter(remaining_ids)
    h = Counter(hand_ids)
    rows = []
    for cid in sorted(t):
        rows.append((cid, t[cid], r.get(cid, 0), h.get(cid, 0)))
    return {"total": len(total_ids), "remaining": len(remaining_ids),
            "hand": len(hand_ids), "rows": rows}


# ---------------------------------------------------------------- 实时读取
def _arr_len(h, RW, arr):
    if not _valid(arr):
        return -1
    raw = RW.read(h, arr + 0x18, 4)
    return _i32(raw, 0) if raw else -1


def _arr_int64(h, RW, arr, k=60):
    n = _arr_len(h, RW, arr)
    if not (0 < n <= 60):
        return []
    raw = RW.read(h, arr + 0x20, min(n, k) * 8)
    if not raw:
        return []
    return [_i32(raw, i * 8) for i in range(min(n, k, len(raw) // 8))]


def _read_state(h, RW, p):
    """读一个 BattleStatePlayerMpo 候选；无效返回 None。"""
    if not _valid(p):
        return None
    deck = _i32(RW.read(h, p + 32, 4) or b"", 0)
    life = _i32(RW.read(h, p + 36, 4) or b"", 0)
    if not (0 <= deck <= 45 and 1 <= life <= 20):
        return None
    hand_arr = _u64(RW.read(h, p + 16, 8) or b"", 0)
    played_arr = _u64(RW.read(h, p + 112, 8) or b"", 0)
    destroyed_arr = _u64(RW.read(h, p + 120, 8) or b"", 0)
    hand = []
    n = _arr_len(h, RW, hand_arr)
    if 0 < n <= 12:
        raw = RW.read(h, hand_arr + 0x20, n * 8) or b""
        for i in range(min(n, len(raw) // 8)):
            e = _u64(raw, i * 8)
            if not _valid(e):
                continue
            raw2 = RW.read(h, e + 0x10, 8) or b""
            if len(raw2) == 8:
                cid = _i32(raw2, 4)          # +0x14 = BattleHandCardMpo.CardId
                if cid:
                    hand.append(cid)
    return {"addr": p, "deckCount": deck, "life": life, "hand": hand,
            "played": [c for c in _arr_int64(h, RW, played_arr) if c],
            "destroyed": [c for c in _arr_int64(h, RW, destroyed_arr) if c]}


def _scan_state(h, RW, budget=64 << 30):
    total = 0
    cands = []
    for base, size in RW.regions(h):
        if total > budget:
            break
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            total += n
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x90:
                arr = np.frombuffer(raw, dtype=np.uint64)
                if len(arr) > 16:
                    deck = arr[4:-12] & 0xFFFFFFFF
                    life = arr[4:-12] >> 32
                    hand = arr[2:-14]
                    played = arr[14:-2]
                    mask = ((deck >= 1) & (deck <= 45)
                            & (life >= 1) & (life <= 20)
                            & (hand > MIN_PTR) & (hand < MAX_PTR)
                            & (played > MIN_PTR) & (played < MAX_PTR))
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p > MIN_PTR:
                            cands.append(p)
            off += n
    return cands


def _scan_deck(h, RW, ids, budget=64 << 30):
    out = []
    total = 0
    for base, size in RW.regions(h):
        if total > budget:
            break
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            total += n
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x100:
                arr = np.frombuffer(raw, dtype=np.uint64)
                if len(arr) > 5:
                    mlen = (arr[3:-1] & 0xFFFFFFFF)
                    mask = (mlen >= 30) & (mlen <= 45)
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p <= MIN_PTR:
                            continue
                        nl = int(mlen[i])
                        rawel = RW.read(h, p + 0x20, min(nl, 45) * 8)
                        if not rawel:
                            continue
                        ids2 = []
                        for k in range(min(nl * 2, len(rawel) // 4)):
                            v = _i32(rawel, k * 4)
                            if v in ids:
                                ids2.append(v)
                        if len(ids2) >= 30:
                            out.append((p, ids2))
            off += n
    return out


def _find_state(h, RW):
    meta = load_meta()
    ids = set(int(k) for k in meta) if meta else set()
    cands = _scan_state(h, RW)
    cands.sort(reverse=True)
    best = None
    for p in cands[:3000]:
        s = _read_state(h, RW, p)
        if not s:
            continue
        real = [c for c in s["hand"] + s["played"] + s["destroyed"]
                if not ids or c in ids]
        if not real:
            continue
        # 选“最像当前局”的：牌库少、有手牌/已打出
        if best is None or (s["deckCount"], len(s["hand"])) < (best["deckCount"],
                                                              len(best["hand"])):
            best = s
    return best


def _scan_deck_runs(h, RW, ids, min_run=30, max_gap=2):
    """暴力扫描：找内存里连续（允许小间隔）30+ 个合法卡表 id 的序列。"""
    try:
        ids_arr = np.array(sorted(int(k) for k in ids), dtype=np.int32)
    except Exception:
        return []
    best = []
    for base, size in RW.regions(h):
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 128:
                a = np.frombuffer(raw, dtype=np.int32)
                mask = np.isin(a, ids_arr)
                if int(mask.sum()) >= min_run:
                    idx = np.nonzero(mask)[0]
                    runs = []
                    start = prev = int(idx[0])
                    for x in idx[1:]:
                        x = int(x)
                        if x - prev <= max_gap:
                            prev = x
                        else:
                            runs.append((start, prev))
                            start = prev = x
                    runs.append((start, prev))
                    for s, e in runs:
                        if e - s + 1 < min_run:
                            continue
                        vals = [int(v) for v in a[s:e + 1] if int(v) in ids]
                        if len(vals) > len(best):
                            best = vals
            off += n
    return best


def _find_deck(h, RW, state):
    meta = load_meta()
    ids = set(int(k) for k in meta) if meta else set()
    if not ids:
        return []
    known = set(state.get("played") or []) | set(state.get("hand") or [])
    known |= set(state.get("destroyed") or [])
    # ① 先进卡组界面/数组头方式
    decks = _scan_deck(h, RW, ids)
    best = None
    if decks:
        best = max(decks, key=lambda x: len(known & set(x[1])), default=None)
    # ② 暴力找连续 id 序列（卡组界面验证可用）
    runs = _scan_deck_runs(h, RW, ids)
    if runs and (best is None or len(known & set(runs)) > len(known & set(best[1]))):
        best = (0, runs)
    if not best:
        return []
    # 游戏外（没有手牌/已打出）时直接认“最长卡组序列”
    if not known:
        return best[1]
    # 只认“和当前手牌/已打出对得上”的卡组：对不上就宁可空，也不显示别的职业的牌
    hit = len(known & set(best[1]))
    need = max(2, len(known) // 2)
    if hit < need:
        return []
    return best[1]


_LAST_SNAP = {"data": None, "at": 0.0}
_LAST_SNAP_LOCK = threading.RLock()
SNAPSHOT_STALE_SEC = 30.0
# 最近一次成功解析出的卡组构成。读取竞态（卡组文件正在被重写、内存缓存刚被清）
# 会让某一帧的 deck_ids 为空，进而把「牌库中」整段算成空 —— 记牌器上看起来
# 就是整列表卡牌消失、过一会儿又回来。这里留一份兜底。
_LAST_ROWS = {"ids": [], "at": 0.0}

# 实时读取的「死因」缓存（frida_live 写在 logs/reader_status.json）
_RS = {"at": 0.0, "txt": ""}


def _reader_status_hint():
    """实时读取为什么没起来 —— 给界面状态栏用的一句话。

    2026-11-13：朋友的机器上只有一句「读取未启动」，分不清是**没找到游戏进程**
    还是**压根没启动读取**，来回猜了好几轮。探针那边现在会把死因落盘，
    这里带出来显示，谁的问题一眼就能看出来。
    """
    now = time.monotonic()
    if now - _RS["at"] < 3.0:
        return _RS["txt"]
    _RS["at"] = now
    txt = ""
    try:
        path = os.path.join(HERE, "logs", "reader_status.json")
        with open(path, encoding="utf-8") as fh:
            st = json.load(fh)
        why = str(st.get("why") or "")
        txt = {"nopid": "没找到游戏进程",
               "nomod": "游戏还没进页面",
               "starting": "正在连接游戏",
               "ok": "读取已断",
               "error": "读取启动失败"}.get(why, "")
        # "ok" 不等于「现在正常」：它只说明**曾经**挂上过。能看到这句空文案
        # 的时候，牌库快照已经读不到了 —— 那就是连上之后又断了（游戏重启、
        # 模拟器换进程、读取器被收掉）。这条不按时间过期：断了就是断了，
        # 2026-11-15 用户朋友的机器上正是只有「读取未启动」这一句，无法判断。
        if txt and why != "ok" \
                and time.time() - float(st.get("at") or 0) / 1000.0 > 300:
            txt = ""            # 太久以前的不算数（上一局/上一次启动留下的）
    except Exception:
        txt = ""
    _RS["txt"] = txt
    return txt


def snapshot(min_refresh=3.0):
    """读常驻读取写出的 logs/deck_live.json，返回记牌器快照。

    读取竞态（frida 正在原子替换文件）或短暂无更新时沿用上一次快照，
    避免记牌器在「对局列表」和「当前卡组」之间来回闪。
    """
    try:
        snap = _snapshot_impl(min_refresh)
    except Exception:
        snap = None
    now = time.monotonic()
    with _LAST_SNAP_LOCK:
        if snap is not None and snap.get("in_battle"):
            _LAST_SNAP["data"] = snap
            _LAST_SNAP["at"] = now
            return dict(snap)
        cached = _LAST_SNAP.get("data")
        age = now - _LAST_SNAP.get("at", 0.0)
        # 只有读取失败/文件替换竞态才沿用缓存；确认文件已过期则视为真实离局。
        transient = (snap is None or "等待常驻读取" in
                     str(snap.get("reason") or ""))
        if transient and cached is not None and age < SNAPSHOT_STALE_SEC:
            return dict(cached)
        if snap is None:
            hint = _reader_status_hint()
            return {"in_battle": False,
                    "reason": "等待常驻读取（logs/deck_live.json）"
                              + ("，" + hint if hint else "")}
        return snap


def _snapshot_impl(min_refresh=3.0):
    """读常驻读取写出的 logs/deck_live.json，返回记牌器快照。"""
    import io
    path = LIVE_PATH
    try:
        with io.open(path, encoding="utf-8") as fh:
            s = json.load(fh)
    except Exception:
        # 读到半写 JSON、文件被替换或瞬时 IO 错误时由 snapshot() 返回缓存。
        return None
    if s.get("idle"):
        return {"in_battle": False, "reason": "等待进入对局"}
    age = time.time() - float(s.get("at") or 0) / 1000.0
    # 对局结束后 frida 不再写 deck_live.json → 10 秒内清空（外层还有缓存兜底，
    # 避免 500ms 写入竞态时来回闪成"当前卡组"）
    if age > 30:
        return {"in_battle": False, "reason": "对局已结束 / 实时读取未在跑"}
    hand = [c for c in s.get("hand") or [] if c]
    played = [c for c in s.get("played") or [] if c]
    destroyed = [c for c in s.get("destroyed") or [] if c]
    deck_ids = _CACHE.get("deck_ids") or []
    from_file = False
    # 手动导入的卡组**优先**：内存扫描缓存可能是旧卡组/别的卡组，
    # 会导致牌库数字不减、显示错误的卡组构成（用户 2026-10-29）
    decks_all = _decks_from_file()
    if decks_all:
        from_file = True
        seen0 = [c for c in (hand + played + destroyed) if c]
        active = _active_name()
        ordered = [x for x in decks_all if x.get("name") == active]
        ordered += [x for x in decks_all if x.get("name") != active]
        if not seen0:
            deck_ids = ordered[0]["cards"]
        else:
            active_item = next(
                (x for x in ordered if x.get("name") == active), None)
            best = None
            for item in ordered:
                names = set(_name_of(c) for c in item["cards"])
                hit = sum(1 for c in seen0
                          if _name_of(c) and _name_of(c) in names)
                if best is None or hit > best[0]:
                    best = (hit, item["cards"])
            # 手牌少时（1~3 张）也要能匹配上：门槛 max(1, len//4)
            need = max(1, len(seen0) // 4)
            if best and best[0] >= need:
                deck_ids = best[1]
            elif active_item:
                deck_ids = active_item["cards"]
    # 这一帧没解析出卡组构成（卡组文件正在被重写 / 缓存刚被清）：先用最近一次
    # 成功解析的那份兜底，别把「牌库中」整段清空 —— 那看起来就是卡牌全消失。
    if not deck_ids:
        now_m = time.monotonic()
        if _LAST_ROWS["ids"] and now_m - _LAST_ROWS["at"] < 180:
            deck_ids = list(_LAST_ROWS["ids"])
    # 有导入卡组时不再做内存扫描（本来就扫不到，白卡 6GB）
    if not deck_ids and not from_file and not _CACHE.get("deck_scanning"):
        _CACHE["deck_scanning"] = True
        try:
            import shadow_warchess_reader as RW
            pid, _ = RW.find_game_pid()
            h = RW.open_proc(pid)
            state = {"addr": s.get("addr"), "deckCount": s.get("deck"),
                     "life": s.get("life"), "pp": s.get("pp"), "maxpp": s.get("maxpp"),
                     "turn": s.get("turn"), "enemy": s.get("enemy"),
                     "hand": hand,
                     "played": played, "destroyed": destroyed}

            def _bg():
                try:
                    ids = _find_deck(h, RW, state)
                    if ids:
                        _CACHE["deck_ids"] = ids
                finally:
                    _CACHE["deck_scanning"] = False

            threading.Thread(target=_bg, daemon=True).start()
        except Exception:
            _CACHE["deck_scanning"] = False
        deck_ids = []
    if not deck_ids:
        # 卡组没扫到：牌库列表留空，手牌由 deck_tracker 单独显示
        return {"in_battle": True, "deckCount": s.get("deck"),
                "life": s.get("life"), "pp": s.get("pp"), "maxpp": s.get("maxpp"),
                     "turn": s.get("turn"), "enemy": s.get("enemy"),
                     "hand": hand, "played": played,
                # seq = 探针的「本局流水号」，记牌器用它保证一局只记一次胜负
                "seq": s.get("seq"),
                "hold": bool(s.get("hold")),
                "destroyed": destroyed, "total": 0,
                "remaining": s.get("deck") or 0, "rows": [],
                "overdraw": int(s.get("overdraw") or 0),
                "returned": bool(s.get("returned"))}
    # 记下这份能用的卡组构成，供上面「读不到卡组」的分支兜底
    _LAST_ROWS["ids"] = list(deck_ids)
    _LAST_ROWS["at"] = time.monotonic()
    from collections import Counter
    total = Counter(deck_ids)
    # 「已经不在牌库里的牌」= 手牌 + 战场 + 墓地。
    #
    # ⚠️ 2026-11-13 用户实测两个 bug，根子都在这里：
    #   ① 【魅惑的魅魔·莉莉姆】3 张、打出 1 张（随后谢幕曲发动、进墓地），
    #      牌库该剩 2 张，界面显示 1 张。原因：以前把「已使用」也算进来，
    #      而这张牌**打出过一次（played）、又死过一次（destroyed）**，
    #      同一张实体牌被扣了两遍 → 少 1 张。
    #   ② 【亡者召还】把墓地的同名随从召到场上，牌库同样被多扣。
    #      那些牌是从墓地回到场上的，本来就没从牌库再抽一次。
    # 手牌 + 战场 + 墓地恰好是「所有离开牌库的实体牌」，各算一次、不会重复；
    # played 是「本局用过哪些牌」的流水记录，会重复计数，只能用来判身份/谢幕曲。
    field_ids = []
    field_cards = []          # 带 atk/life/ev 的完整版本（进化判定用）
    for c in (s.get("field") or []):
        if isinstance(c, dict):          # 战场元素是 {cardId, atk, life, ev}
            try:
                _cid = int(c.get("cardId") or 0)
            except Exception:
                _cid = 0
            if _cid > 0:
                field_cards.append({"cardId": _cid,
                                    "uid": c.get("uid"),
                                    "atk": c.get("atk"),
                                    "life": c.get("life"),
                                    "ev": c.get("ev")})
            c = c.get("cardId")
        try:
            c = int(c)
        except Exception:
            continue
        if c > 0:
            field_ids.append(c)
    left = Counter(hand) + Counter(field_ids) + Counter(destroyed)
    # 同名不同 id（普通/异画版本）按卡名归并，否则 id 版本不同永远差不上
    left_by_name = Counter()
    for cid, n in left.items():
        left_by_name[_name_of(cid) or str(cid)] += n
    hand_by_name = Counter()
    for cid in hand:
        hand_by_name[_name_of(cid) or str(cid)] += 1
    rows = []
    for cid in sorted(total):
        n = total[cid]
        key = _name_of(cid) or str(cid)
        d = min(left_by_name.get(key, 0), n)
        rows.append((cid, n, n - d, hand_by_name.get(key, 0)))
    return {"in_battle": True, "deckCount": s.get("deck"),
            "life": s.get("life"), "pp": s.get("pp"), "maxpp": s.get("maxpp"),
                     "turn": s.get("turn"), "enemy": s.get("enemy"),
                     "hand": hand, "played": played, "field": field_ids,
            # 场地随从的完整信息（含进化状态 ev：0 未进化 / 1 进化 / 2 超进化）
            "field_cards": field_cards,
            "seq": s.get("seq"), "hold": bool(s.get("hold")),
            "newGame": bool(s.get("newGame")),
            # 进化点 / 超进化点（2026-11-17 试验）：探针按字段名反射读到才有值，
            # 读不到就是 None。现在只用于日志，不参与任何判定。
            # evc=本局进化次数、mevc=手动进化次数、evt/sevt=进化的回合数。
            "ep": s.get("ep"), "sep": s.get("sep"),
            "maxep": s.get("maxep"), "maxsep": s.get("maxsep"),
            "evc": s.get("evc"), "mevc": s.get("mevc"),
            "evt": s.get("evt"), "sevt": s.get("sevt"),
            "destroyed": destroyed, "total": len(deck_ids),
            "remaining": s.get("deck") or 0, "rows": rows,
            "overdraw": int(s.get("overdraw") or 0),
            "returned": bool(s.get("returned"))}


def warm_deck():
    """同步扫描卡组并写入缓存（窗口启动前调用，不在 Tk 线程里扫）。"""
    decks = _decks_from_file()
    if decks:
        active = _active_name()
        item = next((x for x in decks if x.get("name") == active), decks[0])
        _CACHE["deck_ids"] = item["cards"]
        return len(item["cards"])
    import io
    try:
        with io.open(LIVE_PATH, encoding="utf-8") as fh:
            s = json.load(fh)
    except Exception:
        return 0
    hand = [c for c in s.get("hand") or [] if c]
    played = [c for c in s.get("played") or [] if c]
    destroyed = [c for c in s.get("destroyed") or [] if c]
    try:
        import shadow_warchess_reader as RW
        pid, _ = RW.find_game_pid()
        h = RW.open_proc(pid)
        state = {"addr": s.get("addr"), "deckCount": s.get("deck"),
                 "life": s.get("life"), "pp": s.get("pp"), "maxpp": s.get("maxpp"),
                     "turn": s.get("turn"), "enemy": s.get("enemy"),
                     "hand": hand,
                 "played": played, "destroyed": destroyed}
        ids = _find_deck(h, RW, state)
        if ids:
            _CACHE["deck_ids"] = ids
        return len(ids)
    except Exception:
        return 0

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗记牌器 · 对象探针（只读，2026-10-21）

目的：在**一局影之诗对战**里，自动找到 `BattlePlayerBase`：
  1. 扫描私有内存里的 il2cpp List 对象（`_items` +0x10 / `_size` +0x18）；
  2. 挑 size 在 30~48 的候选（开局卡组约 40 张）；
  3. 验证元素对象里能否找到 CardId（自动试 `_baseParameter` / `CardId` 偏移）；
  4. 对确认的卡组 List 反查"谁引用它"，反推 BattlePlayerBase 地址与字段偏移。

输出：`logs/deck_probe.txt`（同时打屏）。只读、不写游戏。
用法：
  python -u probe_deck.py                 # 全量
  python -u probe_deck.py --max-scan 2G   # 只扫前 2GB（快速试）
"""
import argparse
import io
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "logs", "deck_probe.txt")

LIST_ITEMS_OFF = 0x10
LIST_SIZE_OFF = 0x18
ARRAY_DATA_OFF = 0x20

MIN_PTR = 0x10000
MAX_PTR = 0x7FFFFFFFFFFF


def u64(raw, off):
    return struct.unpack_from("<Q", raw, off)[0] if len(raw) >= off + 8 else 0


def i32(raw, off):
    return struct.unpack_from("<i", raw, off)[0] if len(raw) >= off + 4 else 0


def valid_ptr(p):
    return MIN_PTR < p < MAX_PTR


def read_list(h, RW, addr, max_n=200):
    raw = RW.read(h, addr + LIST_SIZE_OFF, 4)
    if not raw:
        return []
    n = i32(raw, 0)
    if not (0 <= n <= max_n):
        return []
    raw = RW.read(h, addr + LIST_ITEMS_OFF, 8)
    if not raw:
        return []
    items = u64(raw, 0)
    if not valid_ptr(items):
        return []
    out = []
    for i in range(n):
        raw = RW.read(h, items + ARRAY_DATA_OFF + i * 8, 8)
        if not raw:
            break
        p = u64(raw, 0)
        if valid_ptr(p):
            out.append(p)
    return out


def discover_card_id(h, RW, card_obj, cid_lo=10000000, cid_hi=99999999):
    """
    在卡对象上自动找 `_baseParameter`（指向 CardParameter）和 `CardId` 偏移。
    返回 (param_off, card_id_off, card_id) 或 None。

    2026-10-21 实测调整：国服卡 id 不一定 1 开头（3/5 段都有），
    所以范围放宽到 8 位整数；用「多个元素命中同一偏移」来去误报。
    """
    raw = RW.read(h, card_obj, 0x120)
    if not raw:
        return None
    for po in range(0x08, 0x120, 8):
        param = u64(raw, po)
        if not valid_ptr(param):
            continue
        praw = RW.read(h, param, 0x200)
        if not praw:
            continue
        # CardParameter 的前几个字段是 String*（_cardNameId / _cardName / ...）
        if not (valid_ptr(u64(praw, 0)) and valid_ptr(u64(praw, 8))):
            continue
        for co in range(0, 0x200, 4):
            cid = i32(praw, co)
            if cid_lo <= cid <= cid_hi:
                return po, co, cid
    return None


def module_range(h, name):
    """返回模块 (base, size)；找不到返回 None。"""
    import ctypes
    from ctypes import wintypes as wt
    psapi = ctypes.windll.psapi
    psapi.EnumProcessModules.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                                         ctypes.POINTER(wt.DWORD)]
    psapi.EnumProcessModules.restype = wt.BOOL
    psapi.GetModuleBaseNameW.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                         ctypes.c_wchar_p, wt.DWORD]
    psapi.GetModuleBaseNameW.restype = wt.DWORD
    psapi.GetModuleInformation.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                           ctypes.c_void_p, wt.DWORD]
    psapi.GetModuleInformation.restype = wt.BOOL
    mods = (ctypes.c_void_p * 1024)()
    needed = wt.DWORD()
    if not psapi.EnumProcessModules(h, mods, ctypes.sizeof(mods),
                                    ctypes.byref(needed)):
        return None
    n = needed.value // ctypes.sizeof(ctypes.c_void_p)
    buf = ctypes.create_unicode_buffer(260)
    for i in range(n):
        if not psapi.GetModuleBaseNameW(h, ctypes.c_void_p(mods[i]), buf, 260):
            continue
        if buf.value.lower() != name.lower():
            continue

        class MODULEINFO(ctypes.Structure):
            _fields_ = [("lpBaseOfDll", ctypes.c_void_p),
                        ("SizeOfImage", wt.DWORD),
                        ("EntryPoint", ctypes.c_void_p)]

        mi = MODULEINFO()
        if psapi.GetModuleInformation(h, ctypes.c_void_p(mods[i]),
                                      ctypes.byref(mi), ctypes.sizeof(mi)):
            return mi.lpBaseOfDll, mi.SizeOfImage
    return None


def scan_lists(h, RW, budget, klass_lo=0, klass_hi=0):
    """扫描 List 候选。返回 [(addr, size)]。

    klass_lo/klass_hi：il2cpp 对象头（前 8 字节）必须落在 GameAssembly 模块内，
    用来把“任意内存位置”的误报压掉（2026-10-21 加）。
    """
    t0 = time.time()
    cands = []
    regions = RW.regions(h, private_only=True)
    total = 0
    for base, size in regions:
        if total > budget:
            break
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            total += n
            raw = RW.read(h, base + off, n)
            if not raw or len(raw) < 0x40:
                off += n
                continue
            # 8 字节步长：p=0,8,...；size 在 p+0x18（int32），items 在 p+0x10（u64）
            npos = (len(raw) - 0x20) // 8
            if npos <= 0:
                off += n
                continue
            i32v = np.frombuffer(raw, dtype=np.int32, count=(len(raw) - 0x18) // 4,
                                 offset=0x18)
            sizes = i32v[0::2][:npos]
            u64v = np.frombuffer(raw, dtype=np.uint64, count=(len(raw) - 0x10) // 8,
                                 offset=0x10)
            items = u64v[:npos]
            mask = ((sizes >= 1) & (sizes <= 64)
                    & (items > MIN_PTR) & (items < MAX_PTR))
            if klass_lo:
                kptr = np.frombuffer(raw, dtype=np.uint64)[:npos]
                mask &= (kptr >= klass_lo) & (kptr < klass_hi)
            idx = np.nonzero(mask)[0]
            for i in idx:
                cands.append((base + off + int(i) * 8, int(sizes[i])))
            off += n
    dt = time.time() - t0
    return cands, dt, total


def find_refs(h, RW, target, limit=40):
    """全扫私有内存，找值为 target 的 qword 地址。"""
    hits = []
    for base, size in RW.regions(h, private_only=True):
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 8:
                arr = np.frombuffer(raw, dtype=np.uint64)
                idx = np.nonzero(arr == target)[0]
                for i in idx:
                    hits.append(base + off + int(i) * 8)
                    if len(hits) >= limit:
                        return hits
            off += n
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--max-scan", default="6G", help="最多扫描多少字节（默认 6G）")
    args = ap.parse_args(argv)

    budget = args.max_scan.upper()
    mult = {"K": 1024, "M": 1 << 20, "G": 1 << 30}.get(budget[-1:], 1)
    budget = int(budget[:-1]) * mult if budget[-1] in "KMG" else int(budget)

    fh = io.open(OUT, "a", encoding="utf-8")

    def emit(s=""):
        print(s, flush=True)
        fh.write(s + "\n")
        fh.flush()

    import shadow_warchess_reader as RW
    pid, path = RW.find_game_pid()
    emit("=" * 64)
    emit("记牌器对象探针 %s  pid=%s" % (time.strftime("%H:%M:%S"), pid))
    if not pid:
        emit("没找到游戏进程")
        return 1
    h = RW.open_proc(pid)

    ga = module_range(h, "GameAssembly.dll")
    emit("GameAssembly: %s" % (("%#x + %#x" % ga) if ga else "没找到"))
    emit("扫描 List 候选（预算 %.0f MB）..." % (budget / 1048576.0))
    if ga:
        cands, dt, total = scan_lists(h, RW, budget, ga[0], ga[0] + ga[1])
    else:
        cands, dt, total = scan_lists(h, RW, budget)
    emit("扫描完成：%.1f 秒，%.0f MB，List 候选 %d 个"
         % (dt, total / 1048576.0, len(cands)))
    big = [(a, s) for a, s in cands if 30 <= s <= 48]
    emit("其中 size 30~48（像开局卡组）的有 %d 个" % len(big))
    for a, s in big[:30]:
        emit("  list=%#x size=%d" % (a, s))

    # 当前版本的精确偏移（2026-10-21 Il2CppDumper dump.cs）：
    #   BattleCardBase._baseParameter = +0xA8
    #   CardParameter.CardId          = +0x90
    PARAM_OFF, CARD_ID_OFF = 0xA8, 0x90
    emit("\n验证候选是不是卡组（_baseParameter=+0xA8 / CardId=+0x90）...")
    good = []
    for a, s in big[:60]:
        elems = read_list(h, RW, a)
        if len(elems) < 3:
            continue
        ok, ids = 0, []
        for e in elems:
            raw = RW.read(h, e + PARAM_OFF, 8)
            if not raw:
                continue
            param = u64(raw, 0)
            if not valid_ptr(param):
                continue
            praw = RW.read(h, param + CARD_ID_OFF, 4)
            if praw:
                v = i32(praw, 0)
                if 10000000 <= v <= 99999999:
                    ok += 1
                    ids.append(v)
        if ok >= max(3, len(elems) // 2):
            good.append((a, s, PARAM_OFF, CARD_ID_OFF, ids[:12]))
            emit("  ★ 卡组候选 list=%#x size=%d 命中 %d/%d"
                 % (a, s, ok, len(elems)))
            emit("    卡 id 样本：%s" % ids[:12])
    if not good:
        emit("（没有通过验证的卡组候选）")

    # 对第一个确认的卡组列表反查引用，反推 BattlePlayerBase
    for a, s, po, co, ids in good[:3]:
        emit("\n反查引用：谁指向 list=%#x ..." % a)
        refs = find_refs(h, RW, a)
        emit("  引用点 %d 个（最多 40）" % len(refs))
        for r in refs[:20]:
            # 打印引用点前后 0x40 字节里的非零 qword，人工看偏移
            raw = RW.read(h, r - 0x40, 0x80)
            if not raw:
                continue
            vals = []
            for k in range(0, len(raw) - 8 + 1, 8):
                v = u64(raw, k)
                if valid_ptr(v):
                    vals.append("%#x" % v)
            emit("    ref=%#x  附近指针: %s" % (r, " ".join(vals[:8])))
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗记牌器 · 快速定位卡组列表（只读，2026-10-21）

和 probe_deck.py 的区别：不验证前 60 个候选，而是对**所有** size 30~48 的 List
只读前 6 个元素做 `_baseParameter(+0xA8) -> CardId(+0x90)` 验证。
命中率高的才是卡组列表。

用法： python -u probe_deck_fast.py [--max-scan 3G]
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
OUT = os.path.join(HERE, "logs", "deck_fast.txt")

MIN_PTR = 0x10000
MAX_PTR = 0x7FFFFFFFFFFF
PARAM_OFF = 0xA8
CARD_ID_OFF = 0x90


def u64(raw, off):
    return struct.unpack_from("<Q", raw, off)[0] if len(raw) >= off + 8 else 0


def i32(raw, off):
    return struct.unpack_from("<i", raw, off)[0] if len(raw) >= off + 4 else 0


def valid(p):
    return MIN_PTR < p < MAX_PTR


def module_range(h, name):
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
    if not psapi.EnumProcessModules(h, mods, ctypes.sizeof(mods), ctypes.byref(needed)):
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
        if psapi.GetModuleInformation(h, ctypes.c_void_p(mods[i]), ctypes.byref(mi),
                                      ctypes.sizeof(mi)):
            return mi.lpBaseOfDll, mi.SizeOfImage
    return None


def scan_lists(h, RW, budget, klass_lo, klass_hi):
    t0 = time.time()
    cands = []
    total = 0
    for base, size in RW.regions(h, private_only=True):
        if total > budget:
            break
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            total += n
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x40:
                npos = (len(raw) - 0x20) // 8
                if npos > 0:
                    i32v = np.frombuffer(raw, dtype=np.int32,
                                         count=(len(raw) - 0x18) // 4, offset=0x18)
                    sizes = i32v[0::2][:npos]
                    u64v = np.frombuffer(raw, dtype=np.uint64,
                                         count=(len(raw) - 0x10) // 8, offset=0x10)
                    items = u64v[:npos]
                    mask = ((sizes >= 1) & (sizes <= 64)
                            & (items > MIN_PTR) & (items < MAX_PTR))
                    if klass_lo:
                        kptr = np.frombuffer(raw, dtype=np.uint64)[:npos]
                        mask &= (kptr >= klass_lo) & (kptr < klass_hi)
                    for i in np.nonzero(mask)[0]:
                        cands.append((base + off + int(i) * 8, int(sizes[i])))
            off += n
    return cands, time.time() - t0, total


def head_elems(h, RW, list_addr, k=6):
    raw = RW.read(h, list_addr + 0x10, 8)
    if not raw:
        return []
    items = u64(raw, 0)
    if not valid(items):
        return []
    raw = RW.read(h, items + 0x20, k * 8)
    if not raw:
        return []
    out = []
    for i in range(min(k, len(raw) // 8)):
        p = u64(raw, i * 8)
        if valid(p):
            out.append(p)
    return out


def read_cs_string(h, RW, sptr):
    """读 il2cpp string（+0x10 length，+0x14 UTF-16）。"""
    if not valid(sptr):
        return ""
    raw = RW.read(h, sptr + 0x10, 4)
    if not raw:
        return ""
    n = i32(raw, 0)
    if not (0 < n < 64):
        return ""
    raw = RW.read(h, sptr + 0x14, n * 2)
    if not raw:
        return ""
    return raw.decode("utf-16-le", "replace")


def card_name(h, RW, elem):
    """BattleCardBase._baseParameter(+0xA8) -> CardParameter._cardName(+0x08)。"""
    raw = RW.read(h, elem + PARAM_OFF, 8)
    if not raw:
        return ""
    param = u64(raw, 0)
    if not valid(param):
        return ""
    p = RW.read(h, param + 0x08, 8)
    if not p:
        return ""
    return read_cs_string(h, RW, u64(p, 0))


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--max-scan", default="3G")
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
    pid, _ = RW.find_game_pid()
    emit("=" * 64)
    emit("快速卡组列表定位 %s pid=%s" % (time.strftime("%H:%M:%S"), pid))
    if not pid:
        return 1
    h = RW.open_proc(pid)
    ga = module_range(h, "GameAssembly.dll")
    emit("GameAssembly: %s" % (("%#x + %#x" % ga) if ga else "没找到"))
    cands, dt, total = scan_lists(h, RW, budget, 0, 0)
    big = [(a, s) for a, s in cands if 5 <= s <= 9]
    emit("扫描 %.1f 秒 / %.0f MB，List 候选 %d，其中 size 5~9 = %d"
         % (dt, total / 1048576.0, len(cands), len(big)))

    t0 = time.time()
    good = []
    for a, s in big:
        elems = head_elems(h, RW, a, 6)
        if len(elems) < 4:
            continue
        ok, ids = 0, []
        for e in elems:
            raw = RW.read(h, e + PARAM_OFF, 8)
            if not raw:
                continue
            param = u64(raw, 0)
            if not valid(param):
                continue
            praw = RW.read(h, param + CARD_ID_OFF, 4)
            if praw:
                v = i32(praw, 0)
                if 0 < v < 100000000:
                    ok += 1
                    ids.append(v)
        if ok >= 5:
            names = [card_name(h, RW, e) for e in elems[:6]]
            good.append((a, s, ids))
            emit("★ list=%#x size=%d 命中=%d ids=%s" % (a, s, ok, ids))
            emit("   卡名：%s" % names)
            if len(good) >= 10:
                break
    emit("验证耗时 %.1f 秒，命中 %d 个" % (time.time() - t0, len(good)))
    if not good:
        emit("（没找到 —— 卡 id 可能不是 8 位，或字段偏移不同）")
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

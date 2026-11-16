#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗记牌器 · 从 GameMgr 单例定位 BattlePlayer（只读，2026-10-21）

链路（dump.cs）：
  GameMgr._instance        static 0x0
  GameMgr._battleMgr       +0xB8
  BattleManagerBase.BattlePlayer +0x48
  BattlePlayerBase.HandCardList       +0xA0
  BattlePlayerBase.DeckCardList       +0xA8
  BattlePlayerBase.BattleStartDeckCardList +0xB0
  BattlePlayerBase.CemeteryList       +0xC8
  BattlePlayerBase.BanishList         +0xD0

Il2CppClass 布局（il2cpp.h）：name +0x10，static_fields +0xB8。

用法： python -u probe_game.py
"""
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

MIN_PTR = 0x10000
MAX_PTR = 0x7FFFFFFFFFFF
CLASS_NAME_OFF = 0x10
STATIC_FIELDS_OFF = 0xB8
GM_BATTLE_MGR = 0xB8
BM_PLAYER = 0x48


def u64(raw, off):
    return struct.unpack_from("<Q", raw, off)[0] if len(raw) >= off + 8 else 0


def i32(raw, off):
    return struct.unpack_from("<i", raw, off)[0] if len(raw) >= off + 4 else 0


def valid(p):
    return MIN_PTR < p < MAX_PTR


def find_bytes(h, RW, needle, limit=8):
    hits = []
    for base, size in RW.regions(h):
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            raw = RW.read(h, base + off, n)
            if raw:
                pos = 0
                while True:
                    i = raw.find(needle, pos)
                    if i < 0:
                        break
                    hits.append(base + off + i)
                    pos = i + 1
                    if len(hits) >= limit:
                        return hits
            off += n
    return hits


def find_refs(h, RW, target, limit=8):
    hits = []
    for base, size in RW.regions(h):
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


def list_size(h, RW, addr):
    raw = RW.read(h, addr + 0x18, 4)
    return i32(raw, 0) if raw else -1


def main():
    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    print("=" * 60)
    print("GameMgr 定位 %s pid=%s" % (time.strftime("%H:%M:%S"), pid))
    if not pid:
        return 1
    h = RW.open_proc(pid)

    names = find_bytes(h, RW, b"GameMgr\0", limit=5)
    print("GameMgr 字符串位置:", [hex(x) for x in names])
    if not names:
        print("没找到类名字符串（可能没在对战）")
        return 1

    for naddr in names:
        refs = find_refs(h, RW, naddr, limit=8)
        print("  被引用 %d 次:" % len(refs), [hex(r) for r in refs])
        for r in refs:
            klass = r - CLASS_NAME_OFF
            if klass < MIN_PTR:
                continue
            sf = u64(RW.read(h, klass + STATIC_FIELDS_OFF, 8) or b"", 0)
            if not valid(sf):
                continue
            inst = u64(RW.read(h, sf + 0x0, 8) or b"", 0)
            print("  klass=%#x static_fields=%#x instance=%s"
                  % (klass, sf, hex(inst) if inst else "0"))
            if not valid(inst):
                continue
            bm = u64(RW.read(h, inst + GM_BATTLE_MGR, 8) or b"", 0)
            print("    _battleMgr=%s" % (hex(bm) if bm else "0"))
            if not valid(bm):
                continue
            pl = u64(RW.read(h, bm + BM_PLAYER, 8) or b"", 0)
            print("    BattlePlayer=%s" % (hex(pl) if pl else "0"))
            if not valid(pl):
                continue
            for k in (0xA0, 0xA8, 0xB0, 0xC8, 0xD0):
                v = u64(RW.read(h, pl + k, 8) or b"", 0)
                if valid(v):
                    print("      +%#x -> list=%#x size=%s"
                          % (k, v, list_size(h, RW, v)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

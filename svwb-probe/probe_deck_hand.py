#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：扫描内存里包含当前手牌卡 id 的数组（不限长度）。"""
import json
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
MIN_PTR = 0x10000
MAX_PTR = 0x7FFFFFFFFFFF


def u64(raw, off):
    return struct.unpack_from("<Q", raw, off)[0] if len(raw) >= off + 8 else 0


def i32(raw, off):
    return struct.unpack_from("<i", raw, off)[0] if len(raw) >= off + 4 else 0


def main():
    meta = json.load(open(os.path.join(HERE, "gamedata", "sv_card_meta.json"),
                          encoding="utf-8"))
    ids = set(int(k) for k in meta)
    s = json.load(open(os.path.join(HERE, "logs", "deck_live.json"),
                       encoding="utf-8"))
    hand = [c for c in s.get("hand") or [] if c]
    print("hand:", hand)
    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    h = RW.open_proc(pid)
    total = 0
    hits = 0
    for base, size in RW.regions(h):
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            total += n
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x100:
                arr = np.frombuffer(raw, dtype=np.uint64)
                if len(arr) > 5:
                    mlen = (arr[3:-1] & 0xFFFFFFFF)
                    mask = (mlen >= 20) & (mlen <= 60)
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p <= MIN_PTR:
                            continue
                        nl = int(mlen[i])
                        rawel = RW.read(h, p + 0x20, min(nl, 60) * 4)
                        if not rawel:
                            continue
                        vals4 = []
                        for k in range(min(nl, len(rawel) // 4)):
                            v = i32(rawel, k * 4)
                            if v in ids:
                                vals4.append(v)
                        m = len(set(hand) & set(vals4))
                        if m >= max(2, len(hand) - 1):
                            hits += 1
                            print("★ %#x max=%d match=%d/%d vals=%s"
                                  % (p, nl, m, len(hand), vals4[:16]))
                            if hits >= 10:
                                print("…")
                                return 0
            off += n
    print("done %.0f MB hits=%d" % (total / 1048576.0, hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())

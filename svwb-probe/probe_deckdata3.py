#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 DeckData 实例：+0x40 是 List<int>，size 35~45，元素都是卡表 id。"""
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
    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    print("pid", pid)
    if not pid:
        return 1
    h = RW.open_proc(pid)
    total = 0
    hits = 0
    for base, size in RW.regions(h):
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            total += n
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x48:
                arr = np.frombuffer(raw, dtype=np.uint64)
                if len(arr) > 8:
                    lst = arr[8:-1]          # p+0x40
                    mask = (lst > MIN_PTR) & (lst < MAX_PTR)
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p <= MIN_PTR:
                            continue
                        L = int(lst[i])
                        try:
                            sz = i32(RW.read(h, L + 0x18, 4) or b"", 0)
                            if not (35 <= sz <= 45):
                                continue
                            items = u64(RW.read(h, L + 0x10, 8) or b"", 0)
                            if not (MIN_PTR < items < MAX_PTR):
                                continue
                            rawel = RW.read(h, items + 0x20, sz * 4)
                            if not rawel:
                                continue
                            vals = [i32(rawel, k * 4) for k in range(min(sz, len(rawel) // 4))]
                            good = [v for v in vals if v in ids]
                            if len(good) >= 30:
                                hits += 1
                                print("★ DeckData? p=%#x list=%#x size=%d ids=%d %s"
                                      % (p, L, sz, len(good), good[:10]))
                                if hits >= 8:
                                    return 0
                        except Exception:
                            continue
            off += n
    print("done %.0f MB hits=%d" % (total / 1048576.0, hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())

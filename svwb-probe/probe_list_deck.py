#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 List<int> 对象：+0x18 size 35~45，+0x10 items 指向卡表 id 数组。"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    from collections import Counter
    meta = json.load(open(os.path.join(HERE, "gamedata", "sv_card_meta.json"),
                          encoding="utf-8"))
    ids = set(int(k) for k in meta)
    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    print("pid", pid)
    if not pid:
        return 1
    h = RW.open_proc(pid)
    hits = 0
    for base, size in RW.regions(h):
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x20:
                a = np.frombuffer(raw, dtype=np.uint64) if len(raw) >= 8 else None
                if a is None or len(a) < 4:
                    off += n
                    continue
                # p+0x18 = a[3]; p+0x10 = a[2]
                sz = (a[3:-1] & np.uint64(0xFFFFFFFF)).astype(np.int64)
                items = a[2:-2]
                mask = (sz >= 35) & (sz <= 45) & (items > 0x10000)
                for i in np.nonzero(mask)[0]:
                    p = base + off + int(i) * 8
                    if p <= 0x10000:
                        continue
                    nsz = int(sz[i])
                    it = int(items[i])
                    try:
                        rawel = RW.read(h, it + 0x20, nsz * 4)
                        if not rawel:
                            continue
                        vals = [int(v) for v in np.frombuffer(rawel, dtype=np.int32)]
                        good = [v for v in vals if v in ids]
                        if len(good) >= 30:
                            hits += 1
                            print("★ list=%#x items=%#x size=%d ok=%d %s"
                                  % (p, it, nsz, len(good), good[:10]))
                            if hits >= 8:
                                return 0
                    except Exception:
                        continue
            off += n
    print("hits", hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())

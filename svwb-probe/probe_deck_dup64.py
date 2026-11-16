#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 8 字节 CardId 数组（低 32 位=卡表 id）：找连续 35+ 个。"""
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
    ids = np.array(sorted(int(k) for k in meta), dtype=np.uint64)
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
            if raw and len(raw) >= 256:
                a = np.frombuffer(raw, dtype=np.uint64)
                low = a & np.uint64(0xFFFFFFFF)
                mask = np.isin(low, ids)
                if int(mask.sum()) < 35:
                    off += n
                    continue
                idx = np.nonzero(mask)[0]
                runs = []
                start = prev = int(idx[0])
                for x in idx[1:]:
                    x = int(x)
                    if x - prev <= 2:
                        prev = x
                    else:
                        runs.append((start, prev))
                        start = prev = x
                runs.append((start, prev))
                for s, e in runs:
                    ln = e - s + 1
                    if ln < 35:
                        continue
                    vals = [int(v & 0xFFFFFFFF) for v in a[s:e + 1]
                            if int(v & 0xFFFFFFFF) in set(int(x) for x in ids)]
                    c = Counter(vals)
                    dups = {k: v for k, v in c.items() if v > 1}
                    hits += 1
                    print("★ %#x len=%d dups=%d %s"
                          % (base + off + s * 8, len(vals), len(dups),
                             dict(list(dups.items())[:8])))
                    if hits >= 10:
                        return 0
            off += n
    print("hits", hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())

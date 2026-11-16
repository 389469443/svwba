#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描含重复的卡组数组：连续 35+ 个卡表 id（允许重复），统计重复数。"""
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
    ids = np.array(sorted(int(k) for k in meta), dtype=np.int32)
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
            if raw and len(raw) >= 128:
                a = np.frombuffer(raw, dtype=np.int32)
                mask = np.isin(a, ids)
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
                    vals = [int(v) for v in a[s:e + 1] if int(v) in ids]
                    c = Counter(vals)
                    dups = {k: v for k, v in c.items() if v > 1}
                    if dups or len(vals) >= 39:
                        hits += 1
                        print("★ %#x len=%d dups=%d %s"
                              % (base + off + s * 4, len(vals), len(dups),
                                 dict(list(dups.items())[:6])))
                        if hits >= 10:
                            return 0
            off += n
    print("hits", hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())

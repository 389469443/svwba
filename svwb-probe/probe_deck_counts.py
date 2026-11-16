#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描卡组数量数组：连续 30~45 个 1~3 的 int32，总和 35~45。"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
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
                ok = (a >= 1) & (a <= 3)
                if int(ok.sum()) < 30:
                    off += n
                    continue
                idx = np.nonzero(ok)[0]
                runs = []
                start = prev = int(idx[0])
                for x in idx[1:]:
                    x = int(x)
                    if x == prev + 1:
                        prev = x
                    else:
                        runs.append((start, prev))
                        start = prev = x
                runs.append((start, prev))
                for s, e in runs:
                    ln = e - s + 1
                    if 30 <= ln <= 45:
                        vals = [int(v) for v in a[s:e + 1]]
                        if 35 <= sum(vals) <= 45:
                            hits += 1
                            print("★ %#x len=%d sum=%d vals=%s"
                                  % (base + off + s * 4, ln, sum(vals), vals[:40]))
                            if hits >= 10:
                                return 0
            off += n
    print("hits", hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""暴力扫描：找内存里连续 30+ 个合法卡表 id（卡组/牌库列表，不管对象结构）。"""
import json
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    meta = json.load(open(os.path.join(HERE, "gamedata", "sv_card_meta.json"),
                          encoding="utf-8"))
    ids = np.array(sorted(int(k) for k in meta), dtype=np.int32)
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
            if raw and len(raw) >= 128:
                a = np.frombuffer(raw, dtype=np.int32)
                mask = np.isin(a, ids)
                # 连续 30 个 True
                if mask.sum() < 30:
                    off += n
                    continue
                # 找连续段
                idx = np.nonzero(mask)[0]
                runs = []
                start = idx[0]
                prev = idx[0]
                for x in idx[1:]:
                    if x == prev + 1:
                        prev = x
                    else:
                        runs.append((start, prev))
                        start = x
                        prev = x
                runs.append((start, prev))
                for s, e in runs:
                    ln = e - s + 1
                    if ln >= 30:
                        vals = a[s:e + 1].tolist()
                        hits += 1
                        print("★ %#x run=%d vals=%s"
                              % (base + off + s * 4, ln, vals[:12]))
                        if hits >= 10:
                            print("…")
                            return 0
            off += n
    print("done %.0f MB hits=%d" % (total / 1048576.0, hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())

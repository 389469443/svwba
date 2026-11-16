#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描连续 30+ 合法卡表 id 的序列，并标出和当前手牌的重合。"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    meta = json.load(open(os.path.join(HERE, "gamedata", "sv_card_meta.json"),
                          encoding="utf-8"))
    ids = np.array(sorted(int(k) for k in meta), dtype=np.int32)
    hand = []
    try:
        s = json.load(open(os.path.join(HERE, "logs", "deck_live.json"),
                           encoding="utf-8"))
        hand = [int(c) for c in (s.get("hand") or []) if c]
        print("hand:", hand)
    except Exception as e:
        print("no hand", e)
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
                if mask.sum() < 30:
                    off += n
                    continue
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
                    if ln >= 25:
                        vals = a[s:e + 1].tolist()
                        m = len([v for v in vals if v in hand]) if hand else 0
                        hits += 1
                        print("★ %#x run=%d handhit=%d vals=%s"
                              % (base + off + s * 4, ln, m, vals[:12]))
                        if hits >= 12:
                            print("…")
                            return 0
            off += n
    print("done %.0f MB hits=%d" % (total / 1048576.0, hits))
    return 0


if __name__ == "__main__":
    sys.exit(main())

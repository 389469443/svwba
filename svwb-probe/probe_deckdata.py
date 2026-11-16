#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""影之诗记牌器 · 扫描完整卡组（40 张 8 位卡 id 数组，纯内存，2026-10-21）。"""
import json
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
MIN_PTR = 0x10000
MAX_PTR = 0x7FFFFFFFFFFF


def u64(raw, off):
    return struct.unpack_from("<Q", raw, off)[0] if len(raw) >= off + 8 else 0


def i32(raw, off):
    return struct.unpack_from("<i", raw, off)[0] if len(raw) >= off + 4 else 0


def valid(p):
    return MIN_PTR < p < MAX_PTR


def main():
    meta = {}
    try:
        meta = json.load(open(os.path.join(HERE, "gamedata", "sv_card_meta.json"),
                              encoding="utf-8"))
    except Exception:
        pass
    ids = set(int(k) for k in meta)
    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    print("=" * 60)
    print("卡组扫描 %s pid=%s" % (time.strftime("%H:%M:%S"), pid))
    if not pid:
        return 1
    h = RW.open_proc(pid)

    total = 0
    found = 0
    t0 = time.time()
    for base, size in RW.regions(h):
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            total += n
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x100:
                arr = np.frombuffer(raw, dtype=np.uint64)
                if len(arr) > 5:
                    # 数组：+0x18 max_length(35~45)，+0x20 起元素
                    mlen = (arr[3:-1] & 0xFFFFFFFF)
                    mask = (mlen >= 35) & (mlen <= 45)
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p <= MIN_PTR:
                            continue
                        nlen = int(mlen[i])
                        rawel = RW.read(h, p + 0x20, min(nlen, 45) * 8)
                        if not rawel:
                            continue
                        hits = 0
                        vals = []
                        for k in range(min(nlen, len(rawel) // 8)):
                            v4 = i32(rawel, k * 8)
                            v8 = i32(rawel, k * 8 + 4)
                            if v4 in ids:
                                hits += 1
                                vals.append(v4)
                            elif v8 in ids:
                                hits += 1
                                vals.append(v8)
                        if hits >= 30:
                            found += 1
                            print("★ array=%#x max=%d hits=%d vals=%s"
                                  % (p, nlen, hits, vals[:12]))
                            if found >= 8:
                                print("…停止")
                                return 0
                off += n
            else:
                off += n
    print("扫描 %.1f 秒 / %.0f MB，命中 %d" % (time.time() - t0, total / 1048576.0, found))
    return 0


if __name__ == "__main__":
    sys.exit(main())

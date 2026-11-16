#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗记牌器 · 卡组/牌库/手牌数组扫描（只读，2026-10-21）

扫 il2cpp 数组对象（+0x18 max_length），元素 +0xA8 -> +0x90 = 卡 id。
按 size 分三类打印：
  · 25~45：卡组 / 牌库剩余
  · 4~10 ：手牌
用法： python -u probe_deck3.py [--max-scan 3G]
"""
import argparse
import io
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "logs", "deck3.txt")

MIN_PTR = 0x10000
MAX_PTR = 0x7FFFFFFFFFFF
PARAM_OFF = 0xA8
CARD_ID_OFF = 0x90


def u64(raw, off):
    return struct.unpack_from("<Q", raw, off)[0] if len(raw) >= off + 8 else 0


def i32(raw, off):
    return struct.unpack_from("<i", raw, off)[0] if len(raw) >= off + 4 else 0


def valid(p):
    return MIN_PTR < p < MAX_PTR


def scan(h, RW, budget):
    hits = []
    total = 0
    for base, size in RW.regions(h, private_only=True):
        if total > budget:
            break
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            total += n
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x100:
                arr = np.frombuffer(raw, dtype=np.uint64)
                if len(arr) > 5:
                    mlen = (arr[3:-1] & 0xFFFFFFFF)
                    elems = arr[4:]
                    mask = (((mlen >= 2) & (mlen <= 20) | (mlen >= 25) & (mlen <= 48))
                            & (elems > MIN_PTR) & (elems < MAX_PTR))
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p > MIN_PTR:
                            hits.append((p, int(mlen[i])))
            off += n
    return hits, total


def card_ids(h, RW, arr, n):
    raw = RW.read(h, arr + 0x20, min(n, 60) * 8)
    if not raw:
        return []
    ids = []
    for i in range(min(n, len(raw) // 8)):
        e = u64(raw, i * 8)
        if not valid(e):
            continue
        praw = RW.read(h, e + PARAM_OFF, 8)
        if not praw:
            continue
        param = u64(praw, 0)
        if not valid(param):
            continue
        cidraw = RW.read(h, param + CARD_ID_OFF, 4)
        if cidraw:
            v = i32(cidraw, 0)
            if 0 < v < 100000000:
                ids.append(v)
    return ids


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--max-scan", default="3G")
    args = ap.parse_args(argv)
    budget = args.max_scan.upper()
    mult = {"K": 1024, "M": 1 << 20, "G": 1 << 30}.get(budget[-1:], 1)
    budget = int(budget[:-1]) * mult if budget[-1] in "KMG" else int(budget)

    fh = io.open(OUT, "a", encoding="utf-8")

    def emit(s=""):
        print(s, flush=True)
        fh.write(s + "\n")
        fh.flush()

    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    emit("=" * 64)
    emit("卡组/牌库/手牌扫描 %s pid=%s" % (time.strftime("%H:%M:%S"), pid))
    if not pid:
        return 1
    h = RW.open_proc(pid)
    hits, total = scan(h, RW, budget)
    emit("候选数组 %d（扫描 %.0f MB）" % (len(hits), total / 1048576.0))

    small, big = [], []
    for a, n in hits[:200000]:
        ids = card_ids(h, RW, a, n)
        if len(ids) < max(3, min(n, 10) // 2):
            continue
        if 4 <= n <= 20:
            small.append((a, n, ids))
        elif 25 <= n <= 48:
            big.append((a, n, ids))
    emit("\n=== 手牌类（size 4~12）===")
    for a, n, ids in small[:10]:
        emit("array=%#x size=%d ids=%s" % (a, n, ids[:10]))
    emit("\n=== 卡组/牌库类（size 25~48）===")
    for a, n, ids in big[:15]:
        emit("array=%#x size=%d ids=%s" % (a, n, ids[:12]))
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

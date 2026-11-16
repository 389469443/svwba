#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗记牌器 · 卡组附近数组扫描（只读，2026-10-21）

已知卡组数组地址 A，扫 A±128MB 里的数组，验证元素卡 id，找牌库/手牌。
用法： python -u probe_near.py 0x281e29e8be8
"""
import argparse
import io
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "logs", "near.txt")

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
    ap.add_argument("anchor", nargs="?", default="0x281e29e8be8")
    args = ap.parse_args(argv)
    A = int(args.anchor, 16)
    lo, hi = A - (128 << 20), A + (128 << 20)

    fh = io.open(OUT, "a", encoding="utf-8")

    def emit(s=""):
        print(s, flush=True)
        fh.write(s + "\n")
        fh.flush()

    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    if not pid:
        emit("没有游戏进程")
        return 1
    h = RW.open_proc(pid)
    raw = RW.read(h, A + 0x18, 4)
    emit("anchor %#x size=%s" % (A, i32(raw, 0) if raw else "?"))

    found = []
    for base, size in RW.regions(h, private_only=True):
        if base + size < lo or base > hi:
            continue
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x100:
                arr = np.frombuffer(raw, dtype=np.uint64)
                if len(arr) > 5:
                    mlen = (arr[3:-1] & 0xFFFFFFFF)
                    elems = arr[4:]
                    mask = ((((mlen >= 2) & (mlen <= 20))
                             | ((mlen >= 25) & (mlen <= 48)))
                            & (elems > MIN_PTR) & (elems < MAX_PTR))
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p > MIN_PTR:
                            found.append((p, int(mlen[i])))
            off += n
    emit("附近候选 %d" % len(found))
    seen = 0
    for a, n in found:
        ids = card_ids(h, RW, a, n)
        if len(ids) < max(3, min(n, 8) // 2):
            continue
        emit("array=%#x size=%d ids=%s" % (a, n, ids[:12]))
        seen += 1
        if seen >= 40:
            break
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

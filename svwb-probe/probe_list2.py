#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗记牌器 · List 元素偏移验证（只读，2026-10-21）

疑问：il2cpp List<T>._items 指向数组对象（元素在 +0x20）还是数据区（元素在 +0）？
做法：扫 size 30~48 的 List，用两种偏移读第一个元素，验证 +0xA8 -> +0x90 卡 id。
用法： python -u probe_list2.py [--max-scan 3G]
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
OUT = os.path.join(HERE, "logs", "list2.txt")

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


def card_id(h, RW, e):
    raw = RW.read(h, e + PARAM_OFF, 8)
    if not raw:
        return 0
    param = u64(raw, 0)
    if not valid(param):
        return 0
    raw = RW.read(h, param + CARD_ID_OFF, 4)
    return i32(raw, 0) if raw else 0


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
    emit("List 偏移验证 %s pid=%s" % (time.strftime("%H:%M:%S"), pid))
    if not pid:
        return 1
    h = RW.open_proc(pid)

    total = 0
    found = 0
    t0 = time.time()
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
                    sz = (arr[2:-2] & 0xFFFFFFFF)
                    items = arr[1:-3]
                    mask = ((sz >= 30) & (sz <= 48)
                            & (items > MIN_PTR) & (items < MAX_PTR))
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p <= MIN_PTR:
                            continue
                        itemsv = int(items[i])
                        szv = int(sz[i])
                        # 两种元素偏移
                        e1 = u64(RW.read(h, itemsv, 8) or b"", 0)
                        e2 = u64(RW.read(h, itemsv + 0x20, 8) or b"", 0)
                        c1 = card_id(h, RW, e1) if valid(e1) else 0
                        c2 = card_id(h, RW, e2) if valid(e2) else 0
                        if c1 or c2:
                            emit("list=%#x size=%d items=%#x  at0=%s at20=%s"
                                 % (p, szv, itemsv, c1 or "-", c2 or "-"))
                            found += 1
                            if found >= 30:
                                emit("…（已打印 30 个，停止）")
                                fh.close()
                                return 0
            off += n
    emit("扫描 %.1f 秒，命中 %d" % (time.time() - t0, found))
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗记牌器 · CardParameter 定位探针（只读，2026-10-21）

CardParameter（dump.cs 当前版本）：
  _cardNameId  +0x00  (String*)
  _cardName    +0x08  (String*)
  CardId       +0x90  (int32)

特征：+0x00/+0x08 都是有效指针、+0x90 是 1..1e8 的 int、
且 +0x08 指向的 il2cpp string 能解出中文卡名。

找到 CardParameter 后，反查谁指向它（应该就是 BattleCardBase._baseParameter），
再反查谁引用 BattleCardBase（列表元素）→ List → BattlePlayerBase。

用法： python -u probe_cardparam.py [--max-scan 3G]
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
OUT = os.path.join(HERE, "logs", "cardparam_probe.txt")

MIN_PTR = 0x10000
MAX_PTR = 0x7FFFFFFFFFFF
NAME_ID_OFF = 0x10
NAME_OFF = 0x18
CARD_ID_OFF = 0x90


def u64(raw, off):
    return struct.unpack_from("<Q", raw, off)[0] if len(raw) >= off + 8 else 0


def i32(raw, off):
    return struct.unpack_from("<i", raw, off)[0] if len(raw) >= off + 4 else 0


def valid(p):
    return MIN_PTR < p < MAX_PTR


def read_string(h, RW, sptr):
    if not valid(sptr):
        return ""
    raw = RW.read(h, sptr + 0x10, 4)
    if not raw:
        return ""
    n = i32(raw, 0)
    if not (0 < n < 48):
        return ""
    raw = RW.read(h, sptr + 0x14, n * 2)
    if not raw:
        return ""
    try:
        return raw.decode("utf-16-le", "replace")
    except Exception:
        return ""


def has_cjk(s):
    return any("\u4e00" <= ch <= "\u9fff" for ch in s or "")


def scan(h, RW, budget):
    t0 = time.time()
    total = 0
    hits = []
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
                if len(arr) > 18:
                    v = (arr > MIN_PTR) & (arr < MAX_PTR)
                    tail = arr[18:] & 0xFFFFFFFF
                    # p+0x10 / p+0x18 是 _cardNameId / _cardName（含对象头偏移）
                    mask = (v[2:-16] & v[3:-15]
                            & (tail > 0) & (tail < 100000000))
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p > MIN_PTR:
                            hits.append(p)
            off += n
    return hits, time.time() - t0, total


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
    emit("CardParameter 定位 %s pid=%s" % (time.strftime("%H:%M:%S"), pid))
    if not pid:
        return 1
    h = RW.open_proc(pid)
    hits, dt, total = scan(h, RW, budget)
    emit("扫描 %.1f 秒 / %.0f MB，候选 %d" % (dt, total / 1048576.0, len(hits)))

    good = []
    for p in hits[:50000]:
        name = read_string(h, RW, u64(RW.read(h, p + NAME_OFF, 8) or b"", 0))
        if not name or not has_cjk(name):
            continue
        cid = i32(RW.read(h, p + CARD_ID_OFF, 4) or b"", 0)
        good.append((p, cid, name))
        emit("★ CardParameter=%#x CardId=%d 卡名=%s" % (p, cid, name))
        if len(good) >= 20:
            break
    emit("命中 %d 个" % len(good))
    if not good:
        emit("（没找到 —— 卡名不是中文，或字段偏移不同）")
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

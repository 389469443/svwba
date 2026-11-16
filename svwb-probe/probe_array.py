#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗记牌器 · 数组对象定位（只读，2026-10-21）

il2cpp 数组布局（64 位）：
  +0x00 klass
  +0x10 bounds
  +0x18 max_length (int32/int64)
  +0x20 elements[]

卡组是 List<BattleCardBase>，它的 _items 指向 BattleCardBase[]（max_length 约 40）。
直接扫 max_length 35~45 的数组，读元素验证 `_baseParameter(+0xA8) -> CardId(+0x90)`。

用法： python -u probe_array.py [--max-scan 3G]
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
OUT = os.path.join(HERE, "logs", "array_probe.txt")

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


def read_cs_string(h, RW, sptr):
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


def scan(h, RW, budget, klass_lo, klass_hi):
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
                if len(arr) > 5:
                    mlen = (arr[3:-1] & 0xFFFFFFFF)
                    elems = arr[4:]
                    kptr = arr[:-4]
                    mask = ((mlen >= 35) & (mlen <= 45)
                            & (elems > MIN_PTR) & (elems < MAX_PTR))
                    if klass_lo:
                        mask &= (kptr >= klass_lo) & (kptr < klass_hi)
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
    emit("数组对象定位 %s pid=%s" % (time.strftime("%H:%M:%S"), pid))
    if not pid:
        return 1
    h = RW.open_proc(pid)
    hits, dt, total = scan(h, RW, budget, 0, 0)
    emit("扫描 %.1f 秒 / %.0f MB，max_length 35~45 的数组候选 %d"
         % (dt, total / 1048576.0, len(hits)))

    good = []
    for p in hits[:3000]:
        raw = RW.read(h, p + 0x20, 45 * 8)
        if not raw:
            continue
        ids = []
        for i in range(min(40, len(raw) // 8)):
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
        if len(ids) >= 30:
            names = []
            for i in range(min(10, len(raw) // 8)):
                e = u64(raw, i * 8)
                if not valid(e):
                    continue
                praw = RW.read(h, e + PARAM_OFF, 8)
                if not praw:
                    continue
                param = u64(praw, 0)
                if not valid(param):
                    continue
                sraw = RW.read(h, param + 0x18, 8)
                if sraw:
                    names.append(read_cs_string(h, RW, u64(sraw, 0)))
            # 调试：打印第一个元素的 param 与前后字段原始值
            e0 = u64(raw, 0)
            p0raw = RW.read(h, e0 + PARAM_OFF, 8)
            if p0raw:
                p0 = u64(p0raw, 0)
                q10 = u64(RW.read(h, p0 + 0x10, 8) or b"", 0)
                q18 = u64(RW.read(h, p0 + 0x18, 8) or b"", 0)
                emit("   debug elem=%#x param=%#x 字段+0x10=%#x +0x18=%#x"
                     % (e0, p0, q10, q18))
            good.append((p, len(ids), ids[:10]))
            emit("★ array=%#x 有效元素=%d ids=%s" % (p, len(ids), ids[:10]))
            emit("   卡名：%s" % names)
            if len(good) >= 5:
                break
    emit("命中 %d 个" % len(good))
    if not good:
        emit("（没找到）")
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

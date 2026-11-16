#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗记牌器 · BattlePlayerBase 定位探针（只读，2026-10-21）

精确偏移（Il2CppDumper dump.cs，当前版本）：
  HandCardList            +0xA0
  DeckCardList            +0xA8
  BattleStartDeckCardList +0xB0
  DeckSkillCardList       +0xB8
  ClassAndInPlayCardList  +0xC0
  CemeteryList            +0xC8
  BanishList              +0xD0

特征：这 7 个字段是连续 7 个 List 指针，所以对象 A 的 A+0xA0..A+0xD0
是 7 个连续有效指针。先按这个找 A，再验证 0xB0 的列表 size 30~48、
元素 +0xA8 是 CardParameter、CardParameter+0x90 是卡 id。

用法： python -u probe_player.py [--max-scan 4G]
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
OUT = os.path.join(HERE, "logs", "player_probe.txt")

MIN_PTR = 0x10000
MAX_PTR = 0x7FFFFFFFFFFF

F_HAND = 0xA0
F_DECK = 0xA8
F_START = 0xB0
F_CEM = 0xC8
F_BAN = 0xD0
PARAM_OFF = 0xA8
CARD_ID_OFF = 0x90


def u64(raw, off):
    return struct.unpack_from("<Q", raw, off)[0] if len(raw) >= off + 8 else 0


def i32(raw, off):
    return struct.unpack_from("<i", raw, off)[0] if len(raw) >= off + 4 else 0


def valid(p):
    return MIN_PTR < p < MAX_PTR


def read_ptr(h, RW, addr):
    raw = RW.read(h, addr, 8)
    return u64(raw, 0) if raw else 0


def read_size(h, RW, list_addr):
    raw = RW.read(h, list_addr + 0x18, 4)
    return i32(raw, 0) if raw else -1


def read_elems(h, RW, list_addr, limit=64):
    raw = RW.read(h, list_addr + 0x10, 8)
    if not raw:
        return []
    items = u64(raw, 0)
    if not valid(items):
        return []
    n = read_size(h, RW, list_addr)
    if not (0 <= n <= limit):
        return []
    out = []
    raw = RW.read(h, items + 0x20, n * 8)
    if not raw:
        return []
    for i in range(min(n, len(raw) // 8)):
        p = u64(raw, i * 8)
        if valid(p):
            out.append(p)
    return out


def card_ids(h, RW, elems):
    ids = []
    for e in elems:
        raw = RW.read(h, e + PARAM_OFF, 8)
        if not raw:
            continue
        param = u64(raw, 0)
        if not valid(param):
            continue
        praw = RW.read(h, param + CARD_ID_OFF, 4)
        if praw:
            v = i32(praw, 0)
            if 0 < v < 100000000:
                ids.append(v)
    return ids


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
            if raw and len(raw) >= 8 * 8:
                arr = np.frombuffer(raw, dtype=np.uint64)
                v = (arr > MIN_PTR) & (arr < MAX_PTR)
                if len(v) >= 7:
                    win = np.lib.stride_tricks.sliding_window_view(v, 7)
                    idx = np.nonzero(win.all(axis=1))[0]
                    for i in idx:
                        A = base + off + int(i) * 8 - F_HAND
                        if A > MIN_PTR:
                            hits.append(A)
            off += n
    return hits, time.time() - t0, total


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--max-scan", default="4G")
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
    emit("BattlePlayer 定位探针 %s pid=%s" % (time.strftime("%H:%M:%S"), pid))
    if not pid:
        return 1
    h = RW.open_proc(pid)
    hits, dt, total = scan(h, RW, budget)
    emit("扫描 %.1f 秒 / %.0f MB，候选 A 共 %d 个" % (dt, total / 1048576.0, len(hits)))

    good = []
    for A in hits[:20000]:
        start = read_ptr(h, RW, A + F_START)
        if not valid(start):
            continue
        n = read_size(h, RW, start)
        if not (20 <= n <= 60):
            continue
        elems = read_elems(h, RW, start)
        ids = card_ids(h, RW, elems)
        if len(ids) >= max(3, len(elems) // 2):
            deck = read_size(h, RW, read_ptr(h, RW, A + F_DECK))
            hand = read_size(h, RW, read_ptr(h, RW, A + F_HAND))
            good.append((A, n, deck, hand))
            emit("★ A=%#x 开局=%d 牌库=%s 手牌=%s 卡 id=%s"
                 % (A, n, deck, hand, ids[:10]))
            if len(good) >= 5:
                break
    if not good:
        emit("（没找到 —— 可能不在对战，或特征不成立）")
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

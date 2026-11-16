#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""影之诗记牌器 · 状态扫描 + 手牌卡 id（纯内存，2026-10-21）。

扫描 BattleStatePlayerMpo（DeckCount/Life/HandCards/PlayedCardIds 特征），
命中后读 HandCards 元素（BattleHandCardMpo.CardId +0x14）
和 PlayedCardIds/DestroyedCardIds（int[]，步长 8）。
"""
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


def arr_len(h, RW, arr):
    if not valid(arr):
        return -1
    raw = RW.read(h, arr + 0x18, 4)
    return i32(raw, 0) if raw else -1


def arr_values(h, RW, arr, kind="ptr", k=12):
    n = arr_len(h, RW, arr)
    if not (0 < n <= 60):
        return []
    raw = RW.read(h, arr + 0x20, min(n, k) * 8)
    if not raw:
        return []
    out = []
    for i in range(min(n, k, len(raw) // 8)):
        if kind == "ptr":
            p = u64(raw, i * 8)
            out.append(p if valid(p) else 0)
        else:
            out.append(i32(raw, i * 8))
    return out


def scan_state(h, RW, budget):
    total = 0
    cands = []
    for base, size in RW.regions(h):
        if total > budget:
            break
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            total += n
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x90:
                arr = np.frombuffer(raw, dtype=np.uint64)
                if len(arr) > 16:
                    deck = arr[4:-12] & 0xFFFFFFFF
                    life = arr[4:-12] >> 32
                    hand = arr[2:-14]
                    played = arr[14:-2]
                    mask = ((deck >= 5) & (deck <= 40)
                            & (life >= 5) & (life <= 20)
                            & (hand > MIN_PTR) & (hand < MAX_PTR)
                            & (played > MIN_PTR) & (played < MAX_PTR))
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p > MIN_PTR:
                            cands.append(p)
            off += n
    return cands, total


def read_state(h, RW, p, meta):
    info = {
        "addr": p,
        "deckCount": i32(RW.read(h, p + 32, 4) or b"", 0),
        "life": i32(RW.read(h, p + 36, 4) or b"", 0),
        "cemeteryCount": i32(RW.read(h, p + 88, 4) or b"", 0),
    }
    hand = u64(RW.read(h, p + 16, 8) or b"", 0)
    played = u64(RW.read(h, p + 112, 8) or b"", 0)
    destroyed = u64(RW.read(h, p + 120, 8) or b"", 0)
    info["hand"] = []
    for e in arr_values(h, RW, hand, "ptr", 10):
        if not valid(e):
            continue
        raw = RW.read(h, e + 0x10, 8) or b""
        uid = i32(raw, 0)
        cid = i32(raw, 4)
        if cid == 0:
            raw = RW.read(h, e + 0x14, 4) or b""
            cid = i32(raw, 0)
        info["hand"].append({"uid": uid, "cardId": cid,
                             "name": (meta.get(str(cid)) or {}).get("name")})
    info["played"] = [c for c in arr_values(h, RW, played, "int", 40) if c]
    info["destroyed"] = [c for c in arr_values(h, RW, destroyed, "int", 40) if c]
    return info


def main():
    meta = {}
    try:
        meta = json.load(open(os.path.join(HERE, "gamedata", "sv_card_meta.json"),
                              encoding="utf-8"))
    except Exception:
        pass
    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    print("=" * 60)
    print("状态扫描 %s pid=%s" % (time.strftime("%H:%M:%S"), pid))
    if not pid:
        return 1
    h = RW.open_proc(pid)
    cands, total = scan_state(h, RW, 64 << 30)
    print("候选 %d，扫描 %.0f MB" % (len(cands), total / 1048576.0))
    for p in cands:
        info = read_state(h, RW, p, meta)
        if info["deckCount"] == 0 and info["life"] == 0:
            continue
        if not info["hand"] and not info["played"]:
            continue
        print("★ p=%#x Deck=%d Life=%d Hand=%d Played=%d Destroyed=%d"
              % (p, info["deckCount"], info["life"], len(info["hand"]),
                 len(info["played"]), len(info["destroyed"])))
        print("   hand:", [(c["cardId"], c["name"]) for c in info["hand"][:8]])
        print("   played:", info["played"][:10], "destroyed:", info["destroyed"][:10])
        break
    return 0


if __name__ == "__main__":
    sys.exit(main())

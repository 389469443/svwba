#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""影之诗记牌器 · 实时数据整合（纯内存，2026-10-21）。

策略：
  · 先扫 BattleStatePlayerMpo（DeckCount/Life/HandCards/PlayedCardIds 特征）；
  · 手牌 = HandCards 元素 BattleHandCardMpo.CardId(+0x14)；
  · 已打出/已破坏 = PlayedCardIds/DestroyedCardIds（int64 数组，低 32 位）；
  · 卡组 = 扫 max_length 35~45、元素是卡表 id 的数组（取含已打出卡最多的）。
  · 剩余 = 卡组 - 手牌 - 已打出 - 已破坏（按 id 计数）。
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


def arr_int64(h, RW, arr, k=60):
    n = arr_len(h, RW, arr)
    if not (0 < n <= 60):
        return []
    raw = RW.read(h, arr + 0x20, min(n, k) * 8)
    if not raw:
        return []
    out = []
    for i in range(min(n, k, len(raw) // 8)):
        out.append(i32(raw, i * 8))
    return out


def scan_state(h, RW, budget=64 << 30):
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
    return cands


def read_state(h, RW, p):
    deck = i32(RW.read(h, p + 32, 4) or b"", 0)
    life = i32(RW.read(h, p + 36, 4) or b"", 0)
    hand_arr = u64(RW.read(h, p + 16, 8) or b"", 0)
    played_arr = u64(RW.read(h, p + 112, 8) or b"", 0)
    destroyed_arr = u64(RW.read(h, p + 120, 8) or b"", 0)
    hand = []
    n = arr_len(h, RW, hand_arr)
    if 0 < n <= 12:
        raw = RW.read(h, hand_arr + 0x20, n * 8) or b""
        for i in range(min(n, len(raw) // 8)):
            e = u64(raw, i * 8)
            if not valid(e):
                continue
            raw2 = RW.read(h, e + 0x10, 8) or b""
            if len(raw2) == 8:
                hand.append(i32(raw2, 4))       # +0x14 = CardId
    return {"addr": p, "deckCount": deck, "life": life, "hand": hand,
            "played": [c for c in arr_int64(h, RW, played_arr) if c],
            "destroyed": [c for c in arr_int64(h, RW, destroyed_arr) if c]}


def scan_decks(h, RW, known_ids):
    """找卡组数组：max_length 30~45，元素是卡表 id；返回 [(addr, ids)]。"""
    out = []
    total = 0
    for base, size in RW.regions(h):
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            total += n
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x100:
                arr = np.frombuffer(raw, dtype=np.uint64)
                if len(arr) > 5:
                    mlen = (arr[3:-1] & 0xFFFFFFFF)
                    mask = (mlen >= 30) & (mlen <= 45)
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p <= MIN_PTR:
                            continue
                        nl = int(mlen[i])
                        rawel = RW.read(h, p + 0x20, min(nl, 45) * 8)
                        if not rawel:
                            continue
                        ids = []
                        for k in range(min(nl, len(rawel) // 8)):
                            v4 = i32(rawel, k * 8)
                            v8 = i32(rawel, k * 8 + 4)
                            if v4 in known_ids:
                                ids.append(v4)
                            elif v8 in known_ids:
                                ids.append(v8)
                        if len(ids) >= 30:
                            out.append((p, ids))
            off += n
    return out


def main():
    meta = json.load(open(os.path.join(HERE, "gamedata", "sv_card_meta.json"),
                          encoding="utf-8"))
    ids = set(int(k) for k in meta)
    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    print("pid", pid)
    h = RW.open_proc(pid)
    t0 = time.time()
    cands = scan_state(h, RW)
    print("state candidates", len(cands), "%.1fs" % (time.time() - t0))
    state = None
    for p in cands:
        s = read_state(h, RW, p)
        if s["life"] <= 0:
            continue
        real = [c for c in s["hand"] + s["played"] + s["destroyed"] if c in ids]
        if real and s["deckCount"] <= 12:
            state = s
            break
    if not state:
        print("没找到对局状态")
        return 1
    print("state:", state["addr"], "Deck=%d Life=%d Hand=%d Played=%d Destroyed=%d"
          % (state["deckCount"], state["life"], len(state["hand"]),
             len(state["played"]), len(state["destroyed"])))
    decks = scan_decks(h, RW, ids)
    print("deck arrays:", len(decks))
    played = set(state["played"])
    best = max(decks, key=lambda x: len(played & set(x[1])), default=None)
    if best:
        print("best deck:", hex(best[0]), "n=", len(best[1]))
    else:
        print("no deck")
        return 1
    deck_ids = best[1]
    from collections import Counter
    total = Counter(deck_ids)
    drawn = Counter(state["hand"]) + Counter(state["played"]) + Counter(state["destroyed"])
    print("\n%-20s %s" % ("卡名", "剩余/总数"))
    for cid, n in sorted(total.items()):
        d = min(drawn.get(cid, 0), n)
        nm = (meta.get(str(cid)) or {}).get("name") or ("#%d" % cid)
        print("%-20s %d/%d" % (nm, n - d, n))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：打印所有候选卡组与当前手牌的匹配数。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import deck_reader as D
import shadow_warchess_reader as RW


def main():
    s = json.load(open(os.path.join(HERE, "logs", "deck_live.json"),
                       encoding="utf-8"))
    hand = [c for c in s.get("hand") or [] if c]
    played = set(c for c in s.get("played") or [] if c)
    print("hand:", len(hand), hand)
    pid, _ = RW.find_game_pid()
    h = RW.open_proc(pid)
    ids = set(int(k) for k in D.load_meta())
    decks = D._scan_deck(h, RW, ids)
    print("decks:", len(decks))
    for p, dids in decks:
        mh = len(set(hand) & set(dids))
        mp = len(played & set(dids))
        print("%#x n=%d hand=%d/%d played=%d" % (p, len(dids), mh, len(hand), mp))
        if mh >= max(1, len(hand) - 1):
            print("   ids:", dids)


if __name__ == "__main__":
    main()

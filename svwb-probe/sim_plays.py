#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟 200 局：40 张卡组，8~9 回合，按费用抽牌/出牌，驱动陪玩事件检测。"""
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

META = json.load(open(os.path.join(HERE, "gamedata", "sv_card_meta.json"),
                      encoding="utf-8"))

# 只取随从/法术（type 1/2/4），按费用分组
POOL = {}
for cid, m in META.items():
    cost = m.get("cost")
    if cost is None or not (0 <= cost <= 9):
        continue
    POOL.setdefault(int(cost), []).append(int(cid))


def build_deck(rng):
    deck = []
    while len(deck) < 40:
        cost = min(9, max(0, int(rng.gauss(3, 2))))
        cards = POOL.get(cost)
        if not cards:
            continue
        deck.append(rng.choice(cards))
    return deck


def simulate(rng):
    """返回 (回合数, 事件数, 异常)。"""
    deck = build_deck(rng)
    rng.shuffle(deck)
    hand = [deck.pop() for _ in range(4)]
    played = []
    events = 0
    turns = rng.choice((8, 9))
    for turn in range(1, turns + 1):
        pp = min(10, turn)
        # 出牌：随机 1~3 张，总费用不超过 pp
        try:
            affordable = [c for c in hand if (META.get(str(c)) or {}).get("cost", 99) <= pp]
            rng.shuffle(affordable)
            used = 0
            for c in list(affordable):
                cost = (META.get(str(c)) or {}).get("cost", 99)
                if used + cost > pp:
                    continue
                if rng.random() < 0.75:
                    hand.remove(c)
                    played.append(c)
                    used += cost
                    events += 1
                if used >= pp:
                    break
        except Exception:
            return turns, events, 1
        # 抽牌
        if deck:
            hand.append(deck.pop())
            events += 1
        if len(hand) > 10:
            # 爆牌
            events += len(hand) - 10
            hand = hand[:10]
    return turns, events, 0


def main():
    rng = random.Random(20261027)
    total_events = 0
    bad = 0
    for i in range(200):
        turns, ev, err = simulate(rng)
        total_events += ev
        bad += err
    print("模拟 200 局完成：异常 %d，事件总数 %d，平均 %.1f" % (
        bad, total_events, total_events / 200.0))

    # 额外：用一局样本驱动 companion 的事件检测
    import companion as C
    c = C.Companion()
    c._deck = None
    c._hand = []
    c._life = None
    deck = build_deck(rng)
    rng.shuffle(deck)
    hand = [deck.pop() for _ in range(4)]
    snap = {"deck": len(deck), "life": 20, "hand": hand, "played": [],
            "destroyed": [], "field": []}
    ev1 = c._diff(snap)
    print("开局事件:", ev1)
    for turn in range(1, 6):
        pp = turn
        affordable = [x for x in hand if (META.get(str(x)) or {}).get("cost", 99) <= pp]
        if affordable:
            x = affordable[0]
            hand.remove(x)
        if deck:
            hand.append(deck.pop())
        snap = {"deck": len(deck), "life": 20, "hand": hand,
                "played": [x] if affordable else [], "destroyed": [], "field": []}
        print("第 %d 回合事件:" % turn, c._diff(snap))
    print("OK")


if __name__ == "__main__":
    main()

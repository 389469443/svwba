#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡组码解码器自测：用真实的巫师套卡组码验证，不联网。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import deckcode

# 主人 2026-10-29 发的巫师套（已验证与截图费用分布一致）
CODE = """#巫师(2)#
#指定系列#
#巫师#
1.3.g5Rs.g5Rs.g5Rs.cH1g.cH1g.cH1g.exvm.exvm.exvm.ey9O.ey9O.ey9O.g59G.g59G.g59G.g5hU.g5hU.g5hU.ey9E.ey9E.g5Ok.g5Ok.g5Ok.g5t-.g5t-.g5t-.eyR-.eyR-.eyOs.eyOs.eyOs.g5eM.g5eM.eyeU.eyeU.eyeU.eyee.eyee.g5u8.g5u8
#在游戏中点击【卡牌】-【新牌组】-【使用牌组码】进行粘贴 #"""

PASS = 0
FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS", name, extra)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


def main():
    entries = deckcode.decode(CODE)
    total = sum(n for _, n in entries)
    check("解出 15 种卡", len(entries) == 15, len(entries))
    check("合计 40 张", total == 40, total)
    check("解谜旋律 ×3", (11032310, 3) in entries)
    check("魔女的炼金炉 ×3", (10031210, 3) in entries)
    check("迷人怪兽 ×2", (10732110, 2) in entries)

    # 费用分布与截图直方图 6/12/2/8/5/0/3/4
    meta = json.load(open(os.path.join(HERE, "gamedata", "sv_card_meta.json"),
                          encoding="utf-8"))
    from collections import Counter
    cost = Counter()
    for cid, n in entries:
        v = meta.get(str(cid)) or {}
        cost[v.get("cost")] += n
    check("费用分布 1费=6", cost.get(1) == 6, cost.get(1))
    check("费用分布 2费=12", cost.get(2) == 12, cost.get(2))
    check("费用分布 3费=2", cost.get(3) == 2, cost.get(3))
    check("费用分布 4费=8", cost.get(4) == 8, cost.get(4))
    check("费用分布 5费=5", cost.get(5) == 5, cost.get(5))
    check("费用分布 6费=0", cost.get(6, 0) == 0)
    check("费用分布 7费=3", cost.get(7) == 3, cost.get(7))
    check("费用分布 8费=4", cost.get(8) == 4, cost.get(8))

    # 反向往返
    line = deckcode.encode(entries)
    entries2 = deckcode.decode(line)
    check("编码/解码往返一致", entries2 == entries)

    # 裸码（不带 # 包裹）
    bare = deckcode.decode(CODE.splitlines()[3])
    check("裸码也能解", bare == entries)

    print()
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

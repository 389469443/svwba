#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""爆牌 / 洗回统计逻辑自测（纯函数，不连游戏）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import frida_live as FL

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
    st = {"deck": None, "hand": 0, "mull": False, "over": 0, "ret": False}

    def frame(deck, hand, mull=True):
        p = {"deck": deck, "hand": [0] * hand, "mulliganDone": mull}
        FL._track_draw(st, p)
        return p

    p = frame(20, 4)
    check("初始不爆", p["overdraw"] == 0, p["overdraw"])
    p = frame(19, 5)
    check("正常抽牌不爆", p["overdraw"] == 0, p["overdraw"])
    p = frame(18, 8)
    check("手牌到 8 还没爆", p["overdraw"] == 0, p["overdraw"])
    p = frame(17, 8)
    check("手牌满(8)抽牌 → 爆牌 1", p["overdraw"] == 1, p["overdraw"])
    p = frame(19, 8)
    check("牌库变多 → 洗回标记", p["returned"] is True)
    p = frame(15, 4, mull=False)
    check("换牌阶段重置统计",
          p["overdraw"] == 0 and p["returned"] is False,
          (p["overdraw"], p["returned"]))

    print()
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""胜率悬浮层伤害文案自测：只显示区间 + 最大伤害。"""
import os
import sys

TOOLS = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "dsh-shadowverse-huanjing", "tools"))
sys.path.insert(0, TOOLS)

import overlay  # noqa: E402

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
    data = {"damageDist": {
        "toPlayer": [{"damage": 3, "pPercent": 20},
                     {"damage": 8, "pPercent": 40},
                     {"damage": 12, "pPercent": 5}],
        "toOpponent": [{"damage": 0, "pPercent": 10},
                       {"damage": 9, "pPercent": 60}],
    }}
    text = overlay.build_dist_text(data)
    print(text)
    check("包含我方受伤区间+最大",
          "我方受伤" in text and "3~12 点（最大 12）" in text, text)
    check("包含造成伤害区间+最大",
          "造成伤害" in text and "0~9 点（最大 9）" in text, text)
    check("不再逐档列概率", "%  " not in text and "25%" not in text)

    one = overlay.build_dist_text(
        {"damageDist": {"toPlayer": [{"damage": 5, "pPercent": 100}]}})
    check("单点只显示数值", one.strip() == "我方受伤：5 点", one)

    empty = overlay.build_dist_text({"damageDist": {}, "detail": "空场"})
    check("空分布退回 detail", empty == "空场", empty)

    print()
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

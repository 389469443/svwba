#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deck_reader 离线自测（2026-10-21）

用假内存验证 il2cpp List / 数组 / 卡 id 读取与记牌差分。
不连游戏、不开窗口、不写文件（除无）。
用法： python -u _test_deck_reader.py   （exit 0 = 全过）
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import deck_reader as DR  # noqa: E402

CHECKS = []


def check(name, cond, extra=""):
    CHECKS.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  | " + str(extra)) if extra else ""))
    return bool(cond)


class FakeMem:
    def __init__(self):
        self.buf = bytearray(0x200000)

    def put(self, addr, data):
        self.buf[addr:addr + len(data)] = data

    def put_ptr(self, addr, value):
        self.put(addr, struct.pack("<Q", value))

    def put_i32(self, addr, value):
        self.put(addr, struct.pack("<i", value))

    def read(self, addr, n):
        if addr < 0 or addr + n > len(self.buf):
            return b""
        return bytes(self.buf[addr:addr + n])


def make_list(mem, list_addr, elems):
    """在假内存里构造一个 il2cpp List<BattleCardBase>。"""
    arr = list_addr + 0x1000
    mem.put_ptr(arr - 0x10 + 0x00, 0)          # 数组 object header 占位
    mem.put_ptr(list_addr + DR.LIST_ITEMS_OFF, arr)
    mem.put_i32(list_addr + DR.LIST_SIZE_OFF, len(elems))
    for i, e in enumerate(elems):
        mem.put_ptr(arr + DR.ARRAY_DATA_OFF + i * 8, e)
    return list_addr


def test_list():
    print("\n== 1. il2cpp List 读取 ==")
    mem = FakeMem()
    make_list(mem, 0x100000, [0x102000, 0x103000, 0x104000])
    got = DR.read_list(mem, 0x100000)
    check("元素指针按顺序读出", got == [0x102000, 0x103000, 0x104000], got)
    check("空 List 返回 []", DR.read_list(mem, 0x150000) == [])
    check("越界 size 返回 []",
          DR.read_list(mem, 0x100000, max_n=2) == [])

    mem.put_i32(0x100000 + DR.LIST_SIZE_OFF, -1)
    check("负 size 返回 []", DR.read_list(mem, 0x100000) == [])

    make_list(mem, 0x160000, [0x170000])
    mem.put_ptr(0x160000 + DR.LIST_ITEMS_OFF, 0)
    check("items 指针为 0 → []", DR.read_list(mem, 0x160000) == [])
    check("list_obj=0 → []", DR.read_list(mem, 0) == [])


def test_card_ids():
    print("\n== 2. 卡 id 读取 ==")
    mem = FakeMem()
    make_list(mem, 0x100000, [0x102000, 0x103000])
    # 卡对象 +0x30 -> CardParameter；CardParameter +0x20 -> CardId
    mem.put_ptr(0x102000 + 0x30, 0x105000)
    mem.put_i32(0x105000 + 0x20, 10501110)
    mem.put_ptr(0x103000 + 0x30, 0x106000)
    mem.put_i32(0x106000 + 0x20, 10401120)
    ids = DR.read_card_ids(mem, 0x100000, param_off=0x30, card_id_off=0x20)
    check("读出两张卡 id", ids == [10501110, 10401120], ids)
    check("未校准（None）→ []", DR.read_card_ids(mem, 0x100000) == [])

    mem.put_ptr(0x103000 + 0x30, 0)   # 参数为 0 的卡被跳过
    ids = DR.read_card_ids(mem, 0x100000, param_off=0x30, card_id_off=0x20)
    check("参数指针为 0 的卡跳过", ids == [10501110], ids)


def test_diff():
    print("\n== 3. 记牌差分 ==")
    total = [1, 1, 2, 3]
    remaining = [1, 2]
    drawn, t = DR.diff_counts(total, remaining)
    check("已抽 = 总数 - 剩余", drawn == {1: 1, 3: 1}, drawn)
    check("总数统计正确", t == {1: 2, 2: 1, 3: 1}, t)
    check("全抽完 → 空字典", DR.diff_counts([1], []) == ({1: 1}, {1: 1}))

    snap = DR.summarize(total, remaining, hand_ids=[1])
    check("summarize 的计数", (snap["total"], snap["remaining"], snap["hand"]) == (4, 2, 1),
          snap)
    rows = dict((r[0], r) for r in snap["rows"])
    check("行格式 (cid,总,未抽,手牌)", rows[1] == (1, 2, 1, 1), rows.get(1))
    check("3 号牌未抽且不在手牌", rows[3] == (3, 1, 0, 0), rows.get(3))


def test_snapshot():
    print("\n== 4. 未校准时 snapshot 不报错 ==")
    s = DR.snapshot()
    check("返回 dict 且带 in_battle", isinstance(s, dict) and "in_battle" in s, s)
    check("未对战时 in_battle=False", s.get("in_battle") is False, s)


def main():
    test_list()
    test_card_ids()
    test_diff()
    test_snapshot()
    ok = sum(1 for _n, c in CHECKS if c)
    print("\n结果：%d/%d 通过" % (ok, len(CHECKS)))
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())

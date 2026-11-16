#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_deck 的离线自测（2026-10-21）

用假内存验证 CardParameter/CardId 的自动发现逻辑；不连游戏。
用法： python -u _test_probe_deck.py   （exit 0 = 全过）
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import probe_deck as P  # noqa: E402

CHECKS = []


def check(name, cond, extra=""):
    CHECKS.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  | " + str(extra)) if extra else ""))
    return bool(cond)


class FakeMem:
    def __init__(self):
        self.buf = bytearray(0x400000)

    def put(self, addr, data):
        self.buf[addr:addr + len(data)] = data

    def put_ptr(self, addr, value):
        self.put(addr, struct.pack("<Q", value))

    def put_i32(self, addr, value):
        self.put(addr, struct.pack("<i", value))

    def read(self, h, addr, n):
        if addr < 0 or addr + n > len(self.buf):
            return None
        return bytes(self.buf[addr:addr + n])


def test_discover():
    print("\n== CardParameter / CardId 自动发现 ==")
    mem = FakeMem()
    card = 0x100000
    param = 0x200000
    # 卡对象 +0x30 -> CardParameter
    mem.put_ptr(card + 0x30, param)
    # CardParameter 开头两个字符串指针（有效指针）
    mem.put_ptr(param + 0, 0x300000)
    mem.put_ptr(param + 8, 0x300100)
    # CardId 在 +0x100
    mem.put_i32(param + 0x100, 10501110)

    hit = P.discover_card_id(None, mem, card)
    check("找到 (param_off, card_id_off, cid)",
          hit == (0x30, 0x100, 10501110), hit)

    # 参数对象开头不是字符串指针 → 视为误报
    mem.put_ptr(param + 0, 0)
    check("开头不是有效指针 → None", P.discover_card_id(None, mem, card) is None)

    # 卡 id 范围可调：显式传 1xxxxxxxx 段时 3xxxxxxxx 不算
    mem.put_ptr(param + 0, 0x300000)
    mem.put_i32(param + 0x100, 33556049)
    check("限定 1xxxxxxxx 时 3xxxxxxxx 不算",
          P.discover_card_id(None, mem, card, cid_lo=10000000,
                             cid_hi=19999999) is None)
    check("默认宽范围时 3xxxxxxxx 也算（国服卡 id 段未定）",
          P.discover_card_id(None, mem, card) == (0x30, 0x100, 33556049))

    # 卡对象读不到 → None
    check("卡对象无效 → None", P.discover_card_id(None, mem, 0x3F0000) is None)


def main():
    test_discover()
    ok = sum(1 for _n, c in CHECKS if c)
    print("\n结果：%d/%d 通过" % (ok, len(CHECKS)))
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())

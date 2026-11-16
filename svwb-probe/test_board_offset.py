#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合成内存测试：验证 read_board 读的是 BattleCards(+0x60) 而不是 ShopCardList(+0x50)。

为什么要这个测试：
  游戏没开的时候没法真机验证，但布局是**元数据确定的**，
  可以用一块假的进程内存把两条路径都造出来，检查代码选哪条。

构造的假对象：
    player_obj + 0x50 -> List（3 张"商店卡"，card_id 全无效）
    player_obj + 0x60 -> List（2 张"战场卡"，card_id 有效、星级 4/2）
期望：read_board 返回 +0x60 的那 2 张，且 offset == 0x60。

用法：
    python test_board_offset.py
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import read_world as RW   # noqa: E402

BASE = 0x10000000
CARD_SIZE = 0x200
NULL = 0


class FakeMem:
    """极简假内存：按地址读写固定字节"""

    def __init__(self):
        self.mem = {}

    def write(self, addr, data):
        for i, b in enumerate(data):
            self.mem[addr + i] = b

    def write_q(self, addr, val):
        self.write(addr, struct.pack("<Q", val))

    def write_i(self, addr, val):
        self.write(addr, struct.pack("<i", val))

    def read(self, h, addr, n):
        # 模拟：跨越未写区域返回 None（和真 ReadProcessMemory 失败一致）
        out = bytearray()
        for i in range(n):
            if addr + i not in self.mem:
                return None
            out.append(self.mem[addr + i])
        return bytes(out)

    def zero(self, addr, n):
        """把一块区域填 0（真内存读的时候不会因为没写过就失败）"""
        self.write(addr, b"\x00" * n)

    def write_list(self, list_addr, elems):
        """List<T>：_items@+0x10  _size@+0x18；数组 _bounds@+0x10 _max@+0x18 _data@+0x20"""
        self.zero(list_addr, 0x40)
        arr = list_addr + 0x400
        self.zero(arr, 0x40 + 8 * len(elems))
        self.write_q(list_addr + 0x10, arr)
        self.write_i(list_addr + 0x18, len(elems))
        self.write_i(arr + 0x10, 0)
        self.write_q(arr + 0x18, len(elems))      # 数组长度读的是 8 字节
        for i, e in enumerate(elems):
            self.write_q(arr + 0x20 + 8 * i, e)

    def write_card(self, addr, star, atk, life, card_id, build_addr):
        """BattleCardBase：Star@+0x38 Atk@+0x3c Life@+0x40 BaseParameter@+0x18"""
        self.zero(addr, 0x60)
        self.zero(build_addr, 0x90)
        self.write_i(addr + 0x38, star)
        self.write_i(addr + 0x3C, atk)
        self.write_i(addr + 0x40, life)
        self.write_q(addr + 0x18, build_addr)
        # _buildInfo.CardId 在 [[card+0x10]+0x10]
        self.write_q(addr + 0x10, build_addr)
        self.write_i(build_addr + 0x10, card_id)


def main():
    mem = FakeMem()
    player = BASE + 0x1000

    # ---- 商店列表 @ +0x50：3 张，card_id 无效 ----
    shop_list = BASE + 0x2000
    shop_cards = [BASE + 0x3000 + i * CARD_SIZE for i in range(3)]
    for i, c in enumerate(shop_cards):
        mem.write_card(c, star=1, atk=9, life=9, card_id=0, build_addr=BASE + 0x5000 + i * 0x40)
    mem.write_list(shop_list, shop_cards)
    mem.write_q(player + RW.SHOP_CARDS_OFF, shop_list)

    # ---- 战场列表 @ +0x60：2 张，card_id 有效 ----
    battle_list = BASE + 0x2600
    battle_cards = [BASE + 0x4000 + i * CARD_SIZE for i in range(2)]
    mem.write_card(battle_cards[0], star=4, atk=22, life=12,
                   card_id=10253110, build_addr=BASE + 0x6000)
    mem.write_card(battle_cards[1], star=2, atk=6, life=3,
                   card_id=10223120, build_addr=BASE + 0x6040)
    mem.write_list(battle_list, battle_cards)
    mem.write_q(player + RW.BATTLE_CARDS_OFF, battle_list)

    # ---- 手牌列表 @ +0x58：1 张 ----
    hand_list = BASE + 0x2800
    hand_cards = [BASE + 0x4800]
    mem.write_card(hand_cards[0], star=3, atk=5, life=5,
                   card_id=10604110, build_addr=BASE + 0x6800)
    mem.write_list(hand_list, hand_cards)
    mem.write_q(player + RW.HAND_CARDS_OFF, hand_list)

    # ---- 打补丁：让 read_world 用假内存 ----
    orig_read = RW.read
    RW.read = mem.read
    try:
        ok, cards, off = RW.read_board(None, player, 0)
    finally:
        RW.read = orig_read

    print("=== 合成内存测试：read_board 选哪个列表 ===")
    print(f"  found_list = {ok}")
    print(f"  选中偏移   = {('0x%02x' % off) if off else 'None'}")
    print(f"  读到 {len(cards)} 张：")
    for c in cards:
        print(f"    {c['star']}★ {c['atk']}/{c['life']}  card_id={c['card_id']}")

    passed = 0
    failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  ✓ {name}")
        else:
            failed += 1
            print(f"  ✗ {name} {detail}")

    print()
    check("找到列表", ok is True)
    check("选中 +0x60（BattleCards）", off == RW.BATTLE_CARDS_OFF,
          f"实际 {hex(off) if off else None}")
    check("不是 +0x50（商店）", off != RW.SHOP_CARDS_OFF)
    check("不是 +0x58（手牌）", off != RW.HAND_CARDS_OFF)
    check("数量 = 2", len(cards) == 2, f"实际 {len(cards)}")
    if len(cards) == 2:
        check("第 1 张 4★ 22/12", (cards[0]["star"], cards[0]["atk"],
                                   cards[0]["life"]) == (4, 22, 12))
        check("第 2 张 2★ 6/3", (cards[1]["star"], cards[1]["atk"],
                                 cards[1]["life"]) == (2, 6, 3))
        check("card_id 读对", cards[0]["card_id"] == 10253110)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

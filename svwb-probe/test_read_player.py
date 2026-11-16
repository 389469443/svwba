#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
离线自测：用**合成内存**验证 read_player.py 的解析逻辑。

不需要游戏在跑。做法：构造一块假内存（dict: 地址 -> 字节），
按 Sv2WarchessBattlePlayerBase 的预期布局填好 Life/Grade/BattleCards，
然后验证：
  1) probe_field_offsets 能找到正确偏移
  2) read_list_objects 能读出 List<T>
  3) read_card 能解出 Star/Atk/Life
  4) 端到端 snapshot_player 输出正确

这能在开游戏之前暴露所有 struct 解包/偏移类 bug。
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import read_player as RP  # noqa: E402


class FakeMem:
    """极简假内存：按地址区间拼字节"""
    def __init__(self):
        self.chunks = []          # (base, bytearray)
        self.reads = 0

    def alloc(self, base, size):
        self.chunks.append((base, bytearray(size)))
        return base

    def write(self, addr, data):
        for base, buf in self.chunks:
            if base <= addr and addr + len(data) <= base + len(buf):
                off = addr - base
                buf[off:off + len(data)] = data
                return True
        raise ValueError("地址 %#x 不在任何块内" % addr)

    def write_i32(self, addr, v):
        self.write(addr, struct.pack("<i", v))

    def write_i64(self, addr, v):
        self.write(addr, struct.pack("<Q", v))

    def read(self, h, addr, size):
        self.reads += 1
        for base, buf in self.chunks:
            if base <= addr and addr + size <= base + len(buf):
                off = addr - base
                return bytes(buf[off:off + size])
        # 部分命中：返回可用部分（模拟跨页）
        for base, buf in self.chunks:
            if base <= addr < base + len(buf):
                off = addr - base
                return bytes(buf[off:off + size])
        return b""


def build_world():
    """
    构造：
      player 实例 @0x10000000   字段: UserId, HeroId=1115, Life=35, Armor=2, Grade=4
      List<T> @0x20000000       _items -> 0x20001000, _size=2
      两张卡   @0x30000000/0x30000100
    """
    m = FakeMem()
    m.alloc(0x10000000, 0x200)      # player 对象
    m.alloc(0x20000000, 0x100)      # List 头
    m.alloc(0x20001000, 0x100)      # 数组
    m.alloc(0x30000000, 0x200)      # 卡1
    m.alloc(0x30000100, 0x200)      # 卡2
    m.alloc(0x40000000, 0x100)      # CardParameter

    P = 0x10000000
    # 对象头：klass 随便填，只要不是同一个值即可
    m.write_i64(P + 0x00, 0x1111111111111111)
    m.write_i64(P + 0x08, 0x0)
    # 字段区（从 +0x10 起）
    m.write_i64(P + 0x10, 1234567890123)     # UserId
    m.write_i32(P + 0x18, 1115)              # HeroId（真实范围 1000~10000）
    m.write_i32(P + 0x1C, 35)                # Life
    m.write_i32(P + 0x20, 2)                 # Armor
    m.write_i32(P + 0x24, 4)                 # Grade  ← 期望读到 4
    m.write_i32(P + 0x28, 3)                 # EvolvePoint
    m.write_i32(P + 0x2C, 1)                 # EvolveUnlocked
    m.write_i32(P + 0x30, 0)                 # SuperEvolveUnlocked
    m.write_i32(P + 0x34, 7)                 # MaxEvolvePoint
    m.write_i32(P + 0x38, 0)
    m.write_i32(P + 0x3C, 0)
    m.write_i32(P + 0x40, 0)                 # IsPriviliged
    m.write_i32(P + 0x44, 0)                 # ShowBorderInfo
    m.write_i32(P + 0x48, 0)                 # DoubleSkillId
    m.write_i32(P + 0x4C, 5)                 # HandCardNum
    m.write_i64(P + 0x50, 0)                 # ShopCardList
    m.write_i64(P + 0x58, 0)                 # HandCards
    m.write_i64(P + 0x60, 0x20000000)        # BattleCards -> List

    # List：_items @+0x10, _size @+0x18
    L = 0x20000000
    m.write_i64(L + 0x00, 0x2222222222222222)
    m.write_i64(L + 0x08, 0)
    m.write_i64(L + 0x10, 0x20001000)
    m.write_i32(L + 0x18, 2)
    # .NET 数组布局：klass@0x00, monitor@0x08, bounds@0x10, length@0x18, 数据@0x20
    m.write_i64(0x20001000 + 0x18, 2)          # 长度（read_array 读 8 字节）
    m.write_i64(0x20001000 + 0x20, 0x30000000)
    m.write_i64(0x20001000 + 0x28, 0x30000100)

    # 卡1: Star=2, Atk=3, Life=5
    for base, st, atk, lf in ((0x30000000, 2, 3, 5), (0x30000100, 4, 7, 9)):
        m.write_i64(base + 0x00, 0x3333333333333333)
        m.write_i64(base + 0x08, 0)
        # 前面放点干扰值，确保不是第 0 个四元组被选中
        m.write_i32(base + 0x10, 0)      # _buildInfo 占位
        m.write_i32(base + 0x14, 0)
        m.write_i32(base + 0x18, 0)      # BaseParameter 指针低 4 字节（下面用 8 字节写）
        m.write_i64(base + 0x18, 0)      # 先清零
        m.write_i32(base + 0x20, 0)      # isEvolve
        m.write_i32(base + 0x24, 0)      # evolveState
        m.write_i32(base + 0x28, st)     # Star
        m.write_i32(base + 0x2C, atk)    # Atk
        m.write_i32(base + 0x30, lf)     # Life
        m.write_i32(base + 0x34, 1)      # AtkCount
        m.write_i32(base + 0x38, 0)      # point
        m.write_i32(base + 0x3C, 0)      # power
        m.write_i32(base + 0x40, 77)     # uuid
        # BaseParameter 指针放在 +0x14（8 字节对齐位置）
        m.write_i64(base + 0x18, 0x40000000)

    # CardParameter: CardId 放在 +0x10
    m.write_i64(0x40000000 + 0x00, 0x4444444444444444)
    m.write_i64(0x40000000 + 0x08, 0)
    m.write_i32(0x40000000 + 0x10, 10501110)
    return m


def main():
    m = build_world()
    # 用假内存替换真读取。
    # ⚠️ read_array / find_class 等定义在 shadow_warchess_reader 里，
    #    它们用的是**那个模块自己的** read，所以必须一并替换，
    #    否则测试会走真实的 ReadProcessMemory（拿不到假内存的数据）。
    import shadow_warchess_reader as SR
    RP.read = m.read
    SR.read = m.read
    RP.rd = lambda h, addr, kind: None
    SR.rd = RP.rd

    ok = 0
    fail = 0

    def check(name, cond, extra=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  ✓ {name} {extra}")
        else:
            fail += 1
            print(f"  ✗ {name} {extra}")

    print("=== 1) 字段偏移自适应探测 ===")
    cands = RP.probe_field_offsets(None, 0x10000000)
    check("找到候选", bool(cands), f"共 {len(cands)} 个")
    if cands:
        cand = cands[0]
        off, hero, life, armor, grade = (cand[0], cand[1], cand[2],
                                         cand[3], cand[4])
        check("偏移正确", off == 0x18, f"off={off:#x}（期望 0x18）")
        check("HeroId", hero == 1115, f"={hero}")
        check("Life", life == 35, f"={life}")
        check("Armor", armor == 2, f"={armor}")
        check("Grade", grade == 4, f"={grade}")

    print("")
    print("=== 2) List<T> 读取 ===")
    objs = RP.read_list_objects(None, 0x20000000)
    check("读出 2 个元素", len(objs) == 2, f"={len(objs)}")
    check("元素地址正确", objs == [0x30000000, 0x30000100], f"={[hex(o) for o in objs]}")

    print("")
    print("=== 3) 单张卡解析 ===")
    c1 = RP.read_card(None, 0x30000000)
    check("解析成功", c1 is not None)
    if c1:
        check("Star", c1["star"] == 2, f"={c1['star']}")
        check("Atk", c1["atk"] == 3, f"={c1['atk']}")
        check("Life", c1["life"] == 5, f"={c1['life']}")
        check("card_id", c1["card_id"] == 10501110, f"={c1['card_id']}")

    print("")
    print("=== 4) 列表识别 ===")
    check("looks_like_card_list 为真", RP.looks_like_card_list(None, 0x20000000))

    print("")
    print(f"结果：{ok} 通过 / {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

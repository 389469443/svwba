#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自测 hybridclr_layout.py：用合成内存验证「布局反解」是否真的有效。

做法：
  1. 造一个 BattlePlayerBase 形状的假对象，字段按**非标准布局**摆放
     （故意用"按宽度降序"，即 decl 猜不到的那种）
  2. 给几个已知值（life / grade / hero_id / uid）
  3. 调用工具的四种布局假设 + 暴力反解
  4. 断言：暴力反解能把真实偏移找回来
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import hybridclr_layout as HL  # noqa: E402


class FakeMem:
    def __init__(self):
        self.buf = bytearray(0x1000)
        self.base = 0x100000

    def read(self, h, addr, size):
        off = addr - self.base
        if off < 0 or off + size > len(self.buf):
            return b""
        return bytes(self.buf[off:off + size])

    def put(self, addr, fmt, val):
        struct.pack_into(fmt, self.buf, addr - self.base, val)


def main():
    m = FakeMem()
    A = m.base + 0x100          # 假对象地址
    m.put(A, "<Q", 0xDEADBEEF)  # klass
    m.put(A + 8, "<Q", 0)       # monitor

    # 故意用"按宽度降序 + 自然对齐"的布局，模拟 decl 猜不到的情况
    fields = [
        {"name": "<UserId>k__BackingField", "type": "long", "size": 8},
        {"name": "<HeroId>k__BackingField", "type": "int", "size": 4},
        {"name": "<Life>k__BackingField", "type": "int", "size": 4},
        {"name": "<Armor>k__BackingField", "type": "int", "size": 4},
        {"name": "<Grade>k__BackingField", "type": "int", "size": 4},
        {"name": "<EvolvePoint>k__BackingField", "type": "int", "size": 4},
    ]
    # ★ 故意摆成**四种假设都不可能产生**的乱序布局：
    #   decl/width/packed 都是单调递增的，slots 是 8 字节等距，
    #   而这里故意让偏移不单调、间距不规则，才能真正考验反解。
    true_layout = {
        "<UserId>k__BackingField": 0x10,
        "<HeroId>k__BackingField": 0x48,     # 乱放
        "<Life>k__BackingField": 0x24,       # 在 HeroId 之前
        "<Armor>k__BackingField": 0x80,      # 很远
        "<Grade>k__BackingField": 0x34,
        "<EvolvePoint>k__BackingField": 0xC0,
    }

    uid, hero, life, armor, grade = 907732999798, 1106, 24, 3, 6
    m.put(A + true_layout["<UserId>k__BackingField"], "<q", uid)
    m.put(A + true_layout["<HeroId>k__BackingField"], "<i", hero)
    m.put(A + true_layout["<Life>k__BackingField"], "<i", life)
    m.put(A + true_layout["<Armor>k__BackingField"], "<i", armor)
    m.put(A + true_layout["<Grade>k__BackingField"], "<i", grade)

    print("真实布局：")
    for k, v in true_layout.items():
        print(f"   {k:<34} +{v:#04x}")

    # 用假内存替换读取
    HL.read = m.read

    know = {"life": life, "grade": grade, "hero_id": hero}

    print("")
    print("=== 四种布局假设 ===")
    matched = False
    for name, fn in HL.LAYOUTS.items():
        lay, _t = fn(fields)
        hits, tot, detail = HL.verify_layout(None, A, fields, lay, know)
        print(f"  {name:<7} 命中 {hits}/{tot}")
        if hits == tot:
            matched = True
            ok = all(lay[f["name"]] == true_layout[f["name"]] for f in fields)
            print(f"      {'✓ 而且偏移全对' if ok else '✗ 命中但偏移不同（值有重复）'}")

    print("")
    print("=== 暴力反解 ===")
    lay, detail = HL.brute_force(None, A, fields, know)
    if lay is None:
        print(f"  失败：{detail}")
        return 1
    ok = True
    for k, v in lay.items():
        t = true_layout.get(k)
        flag = "✓" if t == v else "✗"
        if t != v:
            ok = False
        print(f"   {k:<34} 解出 +{v:#04x}  真实 +{t:#04x}  {flag}")

    print("")
    if matched and ok:
        print("结果：假设命中 + 反解正确 ✓")
        return 0
    if ok:
        print("结果：反解正确 ✓（假设未命中，说明反解是必要的）")
        return 0
    print("结果：反解失败 ✗")
    return 1


if __name__ == "__main__":
    sys.exit(main())

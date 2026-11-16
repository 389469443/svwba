#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证 BattleCards 到底在 +0x50 还是 +0x60。

背景：
  子代理用元数据算出 BattlePlayerBase 的布局是
      ShopCardList = +0x50
      BattleCards  = +0x60
  而现有 read_world.py 扫 0x10..0x130 取"卡最多"的列表，
  同分时先命中 +0x50 —— 也就是**商店**。

本脚本同时读两个偏移，并读对方 DTO 路径，用真机数据判断：
  1) 战斗中：+0x60 应该 = 我方场上随从数，且卡名/星级能和画面/卡表对上
  2) +0x50 在战斗中通常为空（商店已关闭）或数量不同
  3) WorldData+0x128 -> S_COMBAT_OPPONENT+0x40 -> minions 应等于对方场上随从

用法：
    python verify_offsets.py            # 读一次
    python verify_offsets.py --loop 3   # 每 3 秒读一次
"""
import argparse
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from shadow_warchess_reader import (          # noqa: E402
    find_game_pid, open_proc, read, load_cards, card_name,
    scan_qwords, find_class,
)
import read_world as RW                        # noqa: E402

HEADER = 0x10
SHOP_OFF = 0x50
BATTLE_OFF = 0x60
CACHE_BATTLE_OFF = 0x128
DTO_MINIONS_OFF = 0x40


def q(h, addr):
    d = read(h, addr, 8)
    return struct.unpack("<Q", d)[0] if d and len(d) == 8 else 0


def arr_objects(h, ptr, limit=16):
    """
    读 Il2CppArray<T> 的元素指针。
    数组布局：+0x10 bounds、+0x18 max_length、+0x20 数据区
    """
    if not (0x10000 < ptr < 0x7FFFFFFFFFFF):
        return [], None
    d = read(h, ptr + HEADER, 0x18)
    if not d or len(d) < 0x10:
        return [], None
    bounds = struct.unpack_from("<q", d, 0)[0]
    n = struct.unpack_from("<q", d, 8)[0]
    if not (0 <= n <= 64):
        return [], None
    data = read(h, ptr + 0x20, 8 * n)
    if not data or len(data) < 8 * n:
        return [], None
    return [struct.unpack_from("<Q", data, 8 * i)[0] for i in range(n)], n


def list_objects(h, ptr, limit=16):
    """
    读 List<T>：_items@+0x10 / _size@+0x18 / 数组数据@+0x20
    """
    if not (0x10000 < ptr < 0x7FFFFFFFFFFF):
        return [], None
    d = read(h, ptr + HEADER, 0x10)
    if not d or len(d) < 0x10:
        return [], None
    items = struct.unpack_from("<Q", d, 0)[0]
    size = struct.unpack_from("<i", d, 8)[0]
    if not (0 <= size <= 64):
        return [], None
    return arr_objects(h, items)


def card_id_of(h, obj):
    """卡 ID 在 [[obj+0x10]+0x10]（_buildInfo.CardId）"""
    bi = q(h, obj + 0x10)
    if not (0x10000 < bi < 0x7FFFFFFFFFFF):
        return None
    d = read(h, bi + 0x10, 4)
    if not d:
        return None
    cid = struct.unpack("<i", d)[0]
    return cid if 10000000 <= cid <= 99999999 else None


def dump_list(h, ptr, label, cards_db):
    objs, n = (list_objects(h, ptr) if ptr else ([], None))
    if n is None:
        # 试数组
        objs, n = arr_objects(h, ptr)
    if n is None:
        print(f"  {label:<10} 指针=0x{ptr:x}  读取失败")
        return []
    out = []
    for o in objs:
        c = RW.read_card(h, o)
        cid = card_id_of(h, o) if c else None
        nm = card_name(cards_db, cid) if cid else "?"
        out.append((c, cid, nm))
        if c:
            print(f"  {label:<10} {c['star']}★ {c['atk']}/{c['life']:<4} id={cid}  {nm}")
        else:
            print(f"  {label:<10} （读不出卡对象） obj=0x{o:x}")
    print(f"  {label:<10} 共 {n} 个")
    return out


def verify_once(h, cards_db):
    snap = RW.snapshot(h, cards_db)
    if "error" in snap:
        print("读 WorldData 失败:", snap["error"])
        return
    me = next((p for p in snap.get("players") or [] if p.get("side") == "我方"), None)
    op = next((p for p in snap.get("players") or [] if p.get("side") == "对方"), None)
    if not me:
        print("没读到我方玩家")
        return
    addr = int(me["addr"], 16) if isinstance(me.get("addr"), str) else me.get("addr")
    print(f"\n我方对象 0x{addr:x}  uid={me.get('uid')} 等级={me.get('grade')} 血={me.get('life')}")

    print("\n--- 两个候选列表 ---")
    p50 = q(h, addr + SHOP_OFF)
    p60 = q(h, addr + BATTLE_OFF)
    print(f"  +0x50 (ShopCardList) 指针 = 0x{p50:x}")
    a = dump_list(h, p50, "+0x50", cards_db)
    print(f"  +0x60 (BattleCards)  指针 = 0x{p60:x}")
    b = dump_list(h, p60, "+0x60", cards_db)

    print("\n--- 现有 read_world 读到的（扫描式）---")
    for c in me.get("cards") or []:
        print(f"  扫描式  {c['star']}★ {c['atk']}/{c['life']}  {c.get('name') or '?'}")

    print("\n--- 对方 DTO 路径 ---")
    wd = RW.find_world(h)
    if not wd:
        print("  没找到 WorldData")
        return
    print(f"  WorldData = 0x{wd:x}")
    cache = q(h, wd + CACHE_BATTLE_OFF)
    print(f"  +0x128 CacheBattleResult = 0x{cache:x}")
    if 0x10000 < cache < 0x7FFFFFFFFFFF:
        dto = q(h, cache + DTO_MINIONS_OFF)
        print(f"  +0x40 minions = 0x{dto:x}")
        objs, n = arr_objects(h, dto)
        if n is not None:
            print(f"  minions 数组 {n} 个：")
            for o in objs:
                cid = None
                for off in (0x1C, 0x18, 0x20):
                    d = read(h, o + off, 4)
                    if d:
                        v = struct.unpack("<i", d)[0]
                        if 10000000 <= v <= 99999999:
                            cid = v
                            break
                nm = card_name(cards_db, cid) if cid else "?"
                print(f"    obj=0x{o:x}  card_id={cid}  {nm}")
        else:
            print("  读不出数组")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=float, default=0)
    args = ap.parse_args()
    pid, _ = find_game_pid()
    if not pid:
        print("游戏没启动")
        return 1
    h = open_proc(pid)
    RW.WD_PID[0] = pid
    cards_db = load_cards()
    print(f"进程 {pid}  卡表 {len(cards_db)} 张")
    while True:
        t0 = time.time()
        verify_once(h, cards_db)
        print(f"  [{time.time()-t0:.1f}s]")
        if not args.loop:
            break
        time.sleep(args.loop)
    return 0


if __name__ == "__main__":
    sys.exit(main())

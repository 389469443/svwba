#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
光环归属探针：在用户游戏过程中抓「光环记录出现」的那一刻，落盘上下文供分析。

已知（2026-10-18 实测）：
    +0x00  int  光环卡号     60000020 / 60000030 / 60000040
    +0x04  int  0
    +0x08  ptr  光环卡对象
    +0x10  int  获得回合     （会重复成 8,8 这样两个 int）
三条记录相距 0xF0 字节，说明是"每个光环一个记录"的对象数组。

未知：**这些记录属于哪个玩家**。本探针每次发现记录数量变化时，把
    · 记录地址 / 卡号 / 回合
    · 记录所在内存块的头部（找 klass）
    · 双方玩家对象地址
    · 从两个玩家对象出发能不能走到这些记录
全部写进 logs/_aura_probe.txt，供事后定位归属字段。
"""
import io
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from shadow_warchess_reader import (  # noqa: E402
    find_game_pid, open_proc, load_cards, read, regions,
)
import read_world as RW  # noqa: E402
import numpy as np  # noqa: E402

AURA_IDS = (60000020, 60000030, 60000040)
OUT = os.path.join(HERE, "logs", "_aura_probe.txt")


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    with io.open(OUT, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line, flush=True)


def find_records(h):
    """
    返回 [(记录起点, 卡号, 回合, 卡对象指针)]

    ⚠️ 只做**一遍**内存扫描：三个卡号在同一块数据里一起找。
       之前每个卡号单独扫一遍（3 × 6.4 GB × 10 秒），会把游戏和判定器都拖慢。
    """
    needles = [(struct.pack("<i", aid), aid) for aid in AURA_IDS]
    hits = []
    for base, size in regions(h, private_only=True):
        off = 0
        while off < size:
            n = min(16 << 20, size - off)
            d = read(h, base + off, n)
            if d:
                for nd, aid in needles:
                    pos = 0
                    while True:
                        i = d.find(nd, pos)
                        if i < 0:
                            break
                        hits.append((base + off + i, aid))
                        pos = i + 4
            off += n

    out = []
    for addr, aid in hits:
        if True:
            # 记录起点 = 命中点 - 0x20（命中点正好是 +0x20 的卡号字段）
            base = addr - 0x20
            head = read(h, base, 0x30)
            if not head or len(head) < 0x30:
                continue
            card_ptr = struct.unpack_from("<Q", head, 0x08)[0]
            r1, r2 = struct.unpack_from("<2i", head, 0x10)
            # 签名：+0x08 是合法指针，+0x10/+0x14 是两个相同的小整数（回合）
            if not (0x10000 < card_ptr < 0x7FFFFFFFFFFF):
                continue
            if r1 != r2 or not (1 <= r1 <= 30):
                continue
            out.append((base, aid, r1, card_ptr))
    return out


def reverse_refs(h, lo, hi, limit=12):
    """
    找「值落在 [lo, hi) 区间内」的指针 —— 用来定位**谁持有**这段记录。

    记录本身不被直接指向（数组按值内联），但**容器**一定被某个对象指着，
    所以搜「指向这一整片区域」的指针，就能摸到容器/持有者。
    """
    hits = []
    try:
        for base, size in regions(h, private_only=True):
            off = 0
            while off < size:
                n = min(16 << 20, size - off)
                d = read(h, base + off, n)
                if d and len(d) >= 8:
                    arr = np.frombuffer(d[:len(d) // 8 * 8], dtype="<u8")
                    m = (arr >= lo) & (arr < hi)
                    if m.any():
                        for i in np.nonzero(m)[0]:
                            hits.append((base + off + int(i) * 8, int(arr[i])))
                            if len(hits) >= limit:
                                return hits
                off += n
    except Exception:
        pass
    return hits


def dump(h, recs):
    log("=" * 60)
    log("发现 %d 条光环记录" % len(recs))
    for base, aid, rnd, card in recs:
        log("  记录 %#x  卡号=%d  回合=%d  卡对象=%#x" % (base, aid, rnd, card))
        # 记录往前的内存块头：找 klass
        head = read(h, base - 0x40, 0x40)
        if head:
            for i in range(0, 0x40, 8):
                q = struct.unpack_from("<Q", head, i)[0]
                if 0x10000 < q < 0x7FFFFFFFFFFF:
                    log("    前 %#x: %#018x" % (base - 0x40 + i, q))

    # 反查：谁指向这片记录区域
    if recs:
        lo = min(b for b, _a, _r, _c in recs) - 0x800
        hi = max(b for b, _a, _r, _c in recs) + 0x800
        refs = reverse_refs(h, lo, hi)
        log("  指向记录区域的指针 %d 个:" % len(refs))
        for slot, val in refs[:10]:
            log("    持有者字段 %#x → %#x" % (slot, val))
            blk = read(h, slot - 0x40, 0x80)
            if blk:
                for i in range(0, 0x80, 8):
                    q = struct.unpack_from("<Q", blk, i)[0]
                    if 0x10000 < q < 0x7FFFFFFFFFFF:
                        log("       %#x: %#018x" % (slot - 0x40 + i, q))

    # 玩家对象
    ent = (RW._cache_load() or {}).get(str(RW.WD_PID[0])) or {}
    a0 = int(ent.get("wd_instance") or "0", 16)
    if a0:
        for off in (0x190, 0x198):
            d = read(h, a0 + off, 8)
            if not d:
                continue
            pa = struct.unpack("<Q", d)[0]
            po = RW.read_player_obj(h, pa) or {}
            log("  玩家 +0x%x  addr=%#x hero=%s 血%s uid=%s"
                % (off, pa, po.get("hero_id"), po.get("life"), po.get("uid")))
            # 从玩家对象出发找记录（深度 5）
            targets = {b for b, _a, _r, _c in recs}
            hit = _walk(h, pa, targets, 5)
            log("     可达记录: %s" % (hit or "无"))


def _walk(h, addr, targets, depth, seen=None, path=None):
    if seen is None:
        seen = set()
    if path is None:
        path = []
    if depth <= 0 or addr in seen or len(seen) > 2000:
        return None
    seen.add(addr)
    if addr in targets:
        return path
    blob = read(h, addr, 0x300)
    if not blob:
        return None
    for i in range(0x8, 0x300, 8):
        if i + 8 > len(blob):
            break
        ptr = struct.unpack_from("<Q", blob, i)[0]
        if not (0x10000 < ptr < 0x7FFFFFFFFFFF):
            continue
        r = _walk(h, ptr, targets, depth - 1, seen, path + [hex(i)])
        if r:
            return r
    return None


def main():
    log("探针启动")
    last = None
    pid = 0
    h = None
    while True:
        try:
            cur_pid, _ = find_game_pid()
            if not cur_pid:
                time.sleep(3)
                continue
            if cur_pid != pid:
                pid = cur_pid
                RW.WD_PID[0] = pid
                h = open_proc(pid)
                log("进程 %d" % pid)
            recs = find_records(h)
            sig = tuple(sorted((b, a, r) for b, a, r, _c in recs))
            if sig != last:
                last = sig
                if recs:
                    dump(h, recs)
                else:
                    log("光环记录清空（新一局/结束）")
        except Exception as e:
            log("探针异常: %s" % e)
        time.sleep(3)


if __name__ == "__main__":
    main()

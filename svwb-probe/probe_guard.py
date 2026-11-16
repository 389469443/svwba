#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
守护 BuffState 探针（2026-10-19 增强：默认 diff 模式）

问题：天生带守护的随从能识别（卡表 skills 里有 guard），
     **被效果加上守护**的随从（例：统率的鲁米纳斯骑士）识别不到。
目标：找出「有守护」时置位的位/字段 —— 抓「被加守护的那一瞬间」哪个 int 变了。

做法：
  · 默认 diff：每张卡（按对象地址）记住 +0x44..+0x90 的整数，只在变化时打印
    `+0xNN 旧→新`；这是抓守护位最省事的口径（加守护一定伴随某位 0→1）。
  · --full：保留老行为，阵容一变就打印整行 +0x44..+0x90（人工对照用）。
  · 退出时汇总「哪些偏移发生过 0→1 / 1→0」，并按次数排序 —— 守护位通常只在
    卡表没有 guard 的随从身上出现 0→1。

用法：
  python -u probe_guard.py                 # diff 模式（默认），1s 一帧
  python -u probe_guard.py --full          # 老的全量模式
  python -u probe_guard.py --interval 0.5

输出：logs/guard_probe.txt（同时打屏）。只读、低频。
配合：一局里先摆一只「被效果加守护」的随从（如统率的鲁米纳斯骑士），
     再看哪个偏移对它出现 0→1，而对普通随从从不出现。
"""
import argparse
import io
import json
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "logs", "guard_probe.txt")
VAL_START = 0x44
VAL_LEN = 0x50


def diff_card_vals(old, new, start=VAL_START):
    """返回 [(addr_offset, old, new)]，只列变化。地址偏移用绝对偏移（+0x44 起）。"""
    out = []
    for i, (a, b) in enumerate(zip(old, new)):
        if a != b:
            out.append((start + i * 4, a, b))
    return out


def fmt_diffs(diffs):
    return "  ".join("+0x%02x %d→%d" % (off, a, b) for off, a, b in diffs)


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--full", action="store_true", help="阵容变化时打印完整 +0x44..0x90")
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args(argv)

    fh = io.open(OUT, "a", encoding="utf-8")

    def emit(s=""):
        print(s, flush=True)
        fh.write(s + "\n")
        fh.flush()

    import shadow_warchess_reader as RW
    import read_world as W

    emit("=" * 64)
    emit("守护探针启动 %s（模式=%s，%.2fs/帧）"
         % (time.strftime("%H:%M:%S"), "full" if args.full else "diff", args.interval))

    h = None
    cards = None
    last_sig = None
    prev_vals = {}          # obj -> [int]
    rise = {}               # addr_offset -> 0→1 次数
    fall = {}               # addr_offset -> 1→0 次数

    while True:
        try:
            if h is None:
                pid, _p = RW.find_game_pid()
                if not pid:
                    time.sleep(1.5)
                    continue
                h = RW.open_proc(pid)
                cards = RW.load_cards()
            snap = W.snapshot(h, cards, force_cold=False)
            if snap.get("error"):
                time.sleep(1.5)
                continue
            me = next((p for p in snap.get("players") or []
                       if p.get("side") == "我方"), None)
            if not me:
                time.sleep(1.5)
                continue
            byname = {c.get("name"): c for c in cards.values()}
            rows = []
            for c in me.get("cards") or []:
                nm = str(c.get("name") or "")
                cd = byname.get(nm) or {}
                native = any(s.get("action") == "guard"
                             for s in (cd.get("skills") or []))
                obj = int(c["obj"], 16)
                raw = RW.read(h, obj + VAL_START, VAL_LEN)
                vals = ([struct.unpack_from("<i", raw, i)[0]
                         for i in range(0, VAL_LEN, 4)] if raw else [])
                rows.append((obj, nm, c.get("atk"), c.get("life"), native, vals))

            now = time.strftime("%H:%M:%S")
            sig = tuple((r[0], r[1], r[2], r[3], r[4]) for r in rows)
            if rows and sig != last_sig:
                if args.full:
                    emit("[%s] %d 只" % (now, len(rows)))
                    for obj, nm, atk, life, native, vals in rows:
                        emit("  %-18s %s/%s native_guard=%s" % (nm, atk, life, native))
                        emit("     +0x44..0x90: %s" % vals)
                else:
                    emit("[%s] 阵容/攻血变化（%d 只）" % (now, len(rows)))
                last_sig = sig

            # diff：同一对象地址的 +0x44..0x90 变化
            if not args.full:
                for obj, nm, atk, life, native, vals in rows:
                    old = prev_vals.get(obj)
                    if old is not None and len(old) == len(vals):
                        diffs = diff_card_vals(old, vals)
                        if diffs:
                            emit("[%s] %-18s %s/%s native_guard=%s"
                                 % (now, nm, atk, life, native))
                            emit("        %s" % fmt_diffs(diffs))
                            for off, a, b in diffs:
                                if a == 0 and b == 1:
                                    rise[off] = rise.get(off, 0) + 1
                                elif a == 1 and b == 0:
                                    fall[off] = fall.get(off, 0) + 1
                    prev_vals[obj] = vals

            time.sleep(args.interval)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            emit("[%s] 异常：%s" % (time.strftime("%H:%M:%S"), exc))
            time.sleep(2.0)

    emit("[%s] 汇总：0→1 的偏移（守护位通常在这里）" % time.strftime("%H:%M:%S"))
    for off, n in sorted(rise.items(), key=lambda kv: (-kv[1], kv[0])):
        emit("  ★ +0x%02x 0→1 %d 次（1→0 %d 次）" % (off, n, fall.get(off, 0)))
    if not rise:
        emit("  （没有抓到 0→1；可能这局没有「被效果加守护」，或守护不在 +0x44..0x90）")
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

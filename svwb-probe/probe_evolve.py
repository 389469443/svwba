#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
进化点校准采样器（2026-10-18）

目的：确认"本局已用进化点"到底存在玩家对象的哪个偏移上。
  做法：每 0.5 秒把我方玩家对象 +0x10..+0x60 的整数、场上随从阵容记一行，
  你**吃一个进化点**，我对比前后哪几个数变了 → 立刻能定位。

输出：logs/evolve_calib.csv（时间, 16 个整数, 我方血量, 场上随从卡号）
用法：pythonw probe_evolve.py    （后台跑，Ctrl+C 或 kill 结束）
"""
import csv
import io
import os
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT = os.path.join(HERE, "logs", "evolve_calib.csv")


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    import shadow_warchess_reader as RW
    import read_world as W

    fh = io.open(OUT, "a", newline="", encoding="utf-8")
    w = csv.writer(fh)
    if fh.tell() == 0:
        w.writerow(["time", "hp", "lv"] + ["i%02x" % i for i in range(0x10, 0x60, 4)]
                   + ["board", "note"])
        fh.flush()

    h = None
    cards = None
    last = None
    print("校准采样中（0.5s/次）→ %s" % OUT, flush=True)
    while True:
        try:
            if h is None:
                pid, _p = RW.find_game_pid()
                if pid:
                    h = RW.open_proc(pid)
                    cards = RW.load_cards()
                    print("游戏 PID=%s" % pid, flush=True)
                else:
                    time.sleep(1.0)
                    continue
            snap = W.snapshot(h, cards, force_cold=False)
            if snap.get("error"):
                time.sleep(0.5)
                continue
            me = next((p for p in snap.get("players") or []
                       if p.get("side") == "我方"), None)
            if not me:
                time.sleep(0.5)
                continue
            addr = int(me["obj"], 16)
            raw = RW.read(h, addr + 0x10, 0x50)
            if not raw or len(raw) < 0x50:
                time.sleep(0.5)
                continue
            ints = [struct.unpack_from("<i", raw, i)[0]
                    for i in range(0, 0x50, 4)]
            board = ",".join(str(c.get("card_id")) for c in me.get("cards") or [])
            row = [time.strftime("%H:%M:%S"), me.get("life"), me.get("grade")] + ints + [board]
            cur = tuple(ints)
            note = ""
            if last is not None:
                parts = []
                if cur != last[1]:
                    parts.append("变化 " + " ".join(
                        "+0x%02x:%d→%d" % (0x10 + i * 4, last[1][i], cur[i])
                        for i in range(len(cur)) if cur[i] != last[1][i]))
                if board != last[2]:
                    parts.append("阵容 %s → %s" % (last[2] or "-", board or "-"))
                if parts:
                    note = " | ".join(parts)
                    print(note, flush=True)
            row.append(note)
            w.writerow(row)
            fh.flush()
            last = (row[0], cur, board)
            time.sleep(0.5)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print("异常（继续）：%s" % exc, flush=True)
            time.sleep(1.0)
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

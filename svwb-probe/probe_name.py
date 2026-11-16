#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""影之诗记牌器 · 卡名字段定位（只读，2026-10-21）"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe_array as PA  # noqa: E402


def u64(raw, off):
    return struct.unpack_from("<Q", raw, off)[0] if len(raw) >= off + 8 else 0


def has_cjk(s):
    return any("\u4e00" <= ch <= "\u9fff" for ch in s or "")


def ok_array(h, RW, a):
    raw = RW.read(h, a + 0x20, 45 * 8)
    if not raw:
        return 0
    n = 0
    for i in range(min(40, len(raw) // 8)):
        e = u64(raw, i * 8)
        if not PA.valid(e):
            continue
        praw = RW.read(h, e + PA.PARAM_OFF, 8)
        if not praw:
            continue
        p = u64(praw, 0)
        if not PA.valid(p):
            continue
        if RW.read(h, p + PA.CARD_ID_OFF, 4):
            n += 1
    return n


def main():
    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    if not pid:
        print("没有游戏进程")
        return 1
    h = RW.open_proc(pid)
    hits, _dt, total = PA.scan(h, RW, 3 << 30, 0, 0)
    arr = None
    for a in hits[:3000]:
        if ok_array(h, RW, a) >= 30:
            arr = a
            break
    if not arr:
        print("没找到卡组数组")
        return 1
    raw = RW.read(h, arr + 0x20, 8 * 8)
    elem = u64(raw, 0)
    param = u64(RW.read(h, elem + PA.PARAM_OFF, 8) or b"", 0)
    print("array=%#x elem=%#x param=%#x" % (arr, elem, param))
    for obj, label in ((elem, "elem"), (param, "param")):
        for off in range(0, 0x200, 8):
            s = u64(RW.read(h, obj + off, 8) or b"", 0)
            if not PA.valid(s):
                continue
            txt = PA.read_cs_string(h, RW, s)
            if txt and has_cjk(txt):
                print("  %s+%#04x -> %r" % (label, off, txt))
    return 0


if __name__ == "__main__":
    sys.exit(main())

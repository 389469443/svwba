#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
解析 MasterMemory 的 mastermemory.bytes（MessagePack，2026-10-21）

用法： python -u parse_master.py [--table CardMaster] [--limit 5]
默认：列出所有表名，并打印 CardMaster 的前几条。
"""
import argparse
import io
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.join(HERE, "gamedata", "mastermemory.bytes")


class Reader:
    def __init__(self, b):
        self.b = b
        self.i = 0

    def u8(self):
        v = self.b[self.i]
        self.i += 1
        return v

    def take(self, n):
        v = self.b[self.i:self.i + n]
        self.i += n
        return v

    def unpack(self, fmt):
        v = struct.unpack_from(fmt, self.b, self.i)[0]
        self.i += struct.calcsize(fmt)
        return v


def decode(r):
    b = r.u8()
    if b <= 0x7f:
        return b
    if b >= 0xe0:
        return b - 0x100
    if 0x80 <= b <= 0x8f:
        n = b & 0x0f
        return {decode(r): decode(r) for _ in range(n)}
    if 0x90 <= b <= 0x9f:
        n = b & 0x0f
        return [decode(r) for _ in range(n)]
    if 0xa0 <= b <= 0xbf:
        return r.take(b & 0x1f).decode("utf-8", "replace")
    if b == 0xc0:
        return None
    if b == 0xc2:
        return False
    if b == 0xc3:
        return True
    if b == 0xc4:
        return r.take(r.u8())
    if b == 0xc5:
        return r.take(r.unpack(">H"))
    if b == 0xc6:
        return r.take(r.unpack(">I"))
    if b == 0xca:
        return r.unpack(">f")
    if b == 0xcb:
        return r.unpack(">d")
    if b == 0xcc:
        return r.u8()
    if b == 0xcd:
        return r.unpack(">H")
    if b == 0xce:
        return r.unpack(">I")
    if b == 0xcf:
        return r.unpack(">Q")
    if b == 0xd0:
        return r.unpack(">b")
    if b == 0xd1:
        return r.unpack(">h")
    if b == 0xd2:
        return r.unpack(">i")
    if b == 0xd3:
        return r.unpack(">q")
    if b == 0xd9:
        return r.take(r.u8()).decode("utf-8", "replace")
    if b == 0xda:
        return r.take(r.unpack(">H")).decode("utf-8", "replace")
    if b == 0xdb:
        return r.take(r.unpack(">I")).decode("utf-8", "replace")
    if b == 0xdc:
        return [decode(r) for _ in range(r.unpack(">H"))]
    if b == 0xdd:
        return [decode(r) for _ in range(r.unpack(">I"))]
    if b == 0xde:
        return {decode(r): decode(r) for _ in range(r.unpack(">H"))}
    if b == 0xdf:
        return {decode(r): decode(r) for _ in range(r.unpack(">I"))}
    raise ValueError("unknown tag %#x at %d" % (b, r.i))


def load(path):
    with io.open(path, "rb") as fh:
        raw = fh.read()
    return decode(Reader(raw))


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--file", default=DEFAULT)
    ap.add_argument("--table", default="CardMaster")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--list", action="store_true", help="只列表名")
    args = ap.parse_args(argv)
    data = load(args.file)
    print("type:", type(data).__name__)
    if not isinstance(data, dict):
        print("top-level not dict:", str(data)[:200])
        return 1
    keys = list(data.keys())
    print("tables:", len(keys))
    for k in keys[:60]:
        print("  ", k)
    if args.list:
        return 0
    t = data.get(args.table)
    if t is None:
        print("没有表：", args.table)
        return 1
    print("\n%s type=%s len=%s" % (args.table, type(t).__name__,
                                   len(t) if hasattr(t, "__len__") else "?"))
    sample = t if isinstance(t, list) else [t]
    for row in sample[:args.limit]:
        print(" ", repr(row)[:400])
    return 0


if __name__ == "__main__":
    sys.exit(main())

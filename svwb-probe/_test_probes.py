#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
两个探针的离线自测（2026-10-19）

只测纯函数（解析 / 差分 / 单调性 / 格式化），不连游戏、不读内存、不写真实日志。
用法： python -u _test_probes.py   （exit 0 = 全过）
"""
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import probe_quests_target as PQT  # noqa: E402
import probe_guard as PG  # noqa: E402

CHECKS = []


def check(name, cond, extra=""):
    CHECKS.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  | " + str(extra)) if extra else ""))
    return bool(cond)


def test_parse_offsets():
    print("\n== 1. 候选偏移解析 ==")
    check("0x 前缀 + 逗号", PQT.parse_offsets("0x10,0x20") == [0x10, 0x20])
    check("空格分隔", PQT.parse_offsets("0x10 0x20") == [0x10, 0x20])
    check("十进制", PQT.parse_offsets("16,32") == [16, 32])
    check("空串 → 空列表", PQT.parse_offsets("") == [])
    check("大小写 0X", PQT.parse_offsets("0X5E01C0") == [0x5E01C0])


def test_extract_candidates():
    print("\n== 2. 从差分日志提取候选 ==")
    text = (
        "[23:54:41] ★候选 +0x0f2ba4 变化 3 次: [('23:54:33', 0, 1)]\n"
        "[23:54:45] ★候选 +0x5bb824 变化 3 次: []\n"
        "[23:54:49] ★候选 +0x5bb824 变化 4 次: []\n"
        "[23:54:50] ★候选 +0x5bb8a4 变化 5 次: []\n"
        "[23:54:51] ★候选 +0x5bb8a4 变化 6 次: []\n"
        "[23:54:52] ★候选 +0x5bb8a4 变化 7 次: []\n"
        "普通行不参与 +0x1111\n"
    )
    fd, path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        got = PQT.extract_candidates(path, min_hits=3)
        check(">=3 次的才进候选", got == [0x5BB8A4], got)
        got2 = PQT.extract_candidates(path, min_hits=2)
        check("min_hits 生效（>=2 次）", 0x5BB824 in got2 and 0x0F2BA4 not in got2, got2)
        check("按次数降序", got2 == [0x5BB8A4, 0x5BB824], got2)
        check("文件不存在 → 空列表",
              PQT.extract_candidates(os.path.join(path, "nope.txt")) == [])
    finally:
        os.unlink(path)


def test_diff_offsets():
    print("\n== 3. 候选差分 ==")
    check("首帧不算变化", PQT.diff_offsets([0x10], {0x10: 1}, None) == [])
    check("值变化被列出",
          PQT.diff_offsets([0x10, 0x20], {0x10: 2, 0x20: 5}, {0x10: 1, 0x20: 5}) == [(0x10, 1, 2)])
    check("缺键不报错",
          PQT.diff_offsets([0x10, 0x30], {0x10: 2}, {0x10: 1, 0x30: 9}) == [(0x10, 1, 2)])
    s = PQT.fmt_changes([(0x5E01C0, 3, 4), (0x5BB8A4, 9, 10)])
    check("格式化带偏移与箭头", s == "+0x5e01c0 3→4  +0x5bb8a4 9→10", s)


def test_monotonic():
    print("\n== 4. 单调性判别 ==")
    check("只增 → only-up",
          PQT.monotonic_state([("a", 0), ("b", 1), ("c", 2)]) == "only-up")
    check("相等也算只增",
          PQT.monotonic_state([("a", 2), ("b", 2), ("c", 3)]) == "only-up")
    check("回退 → wraps",
          PQT.monotonic_state([("a", 0), ("b", 3), ("c", 1)]) == "wraps")
    check("单样本 → flat", PQT.monotonic_state([("a", 5)]) == "flat")
    check("None 被忽略",
          PQT.monotonic_state([("a", None), ("b", 1), ("c", 2)]) == "only-up")


def test_guard_diff():
    print("\n== 5. 守护位差分 ==")
    old = [0, 3, 0, 7]
    new = [0, 4, 1, 7]
    diffs = PG.diff_card_vals(old, new)
    check("只列变化位", diffs == [(0x48, 3, 4), (0x4C, 0, 1)], diffs)
    check("没有变化 → 空", PG.diff_card_vals([1, 2], [1, 2]) == [])
    check("长度不等时按短的比", PG.diff_card_vals([0, 0, 0], [0, 0]) == [])
    s = PG.fmt_diffs([(0x4C, 0, 1), (0x50, 2, 0)])
    check("格式化 `+0xNN a→b`", s == "+0x4c 0→1  +0x50 2→0", s)
    check("地址从 0x44 起", PG.diff_card_vals([0], [1]) == [(0x44, 0, 1)])


def main():
    test_parse_offsets()
    test_extract_candidates()
    test_diff_offsets()
    test_monotonic()
    test_guard_diff()
    ok = sum(1 for _n, c in CHECKS if c)
    print("\n结果：%d/%d 通过" % (ok, len(CHECKS)))
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())

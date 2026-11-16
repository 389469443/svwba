#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
任务计数定向确认探针（2026-10-19）

目的：把「进化一次 / 守护死一只 / 梦魇进场」这三种时刻，和候选偏移的 +1 对齐，
     一旦对上，watch_mem 里的任务数就从「文本/推算」切到「直读内存计数器」。

背景：probe_quests.py 是广撒网（8MB 内所有 int 两两比对），噪声极大。
     它 2026-10-18 23:49~00:04 那轮跑出 1300+ 条候选，但真正同步 +1 的集中在：
       · 0x5bb898 / 0x5bb89c / 0x5bb8a0 / 0x5bb824 / 0x5bb8a4
       · 0x5e01c0 / 0x5e0d00 / 0x5e4370 / 0x5e4970
       · 0x5b4700 / 0x5c1b50
     本脚本只盯这些（或你给的）偏移，每帧打印变化，并统计「是否只增不减」。

用法：
  python -u probe_quests_target.py                     # 内置候选，0.5s 一帧
  python -u probe_quests_target.py --scan-diff         # 重新从 quest_diff.txt 提取候选
  python -u probe_quests_target.py --offsets 0x5bb8a4,0x5e01c0
  python -u probe_quests_target.py --interval 0.4 --window 8M
  python -u probe_quests_target.py --baseline          # 只打一次当前值，然后退出

输出：logs/quest_confirm.txt（同时打屏）。只读，不写游戏、不改 WorldData。
配合：打一局，依次做「进化一次」「让一个守护随从死掉」「让梦魇进场」，
     每次动作前后看一眼哪几个候选 +1，那就是对应任务的计数器。
"""
import argparse
import io
import json
import os
import re
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DIFF_LOG = os.path.join(HERE, "logs", "quest_diff.txt")
CONFIRM_LOG = os.path.join(HERE, "logs", "quest_confirm.txt")
WORLD_CACHE = os.path.join(HERE, "logs", "_world_cache.json")

# 相对 WorldData 的候选偏移（来自 2026-10-18 差分探针，出现 >=2 次且曾同步 +1）
DEFAULT_OFFSETS = [
    0x5bb824, 0x5bb898, 0x5bb89c, 0x5bb8a0, 0x5bb8a4,
    0x5e01c0, 0x5e0d00, 0x5e4370, 0x5e4970,
    0x5b4700, 0x5c1b50,
]

_CAND_RE = re.compile(r"★候选 \+0x([0-9a-fA-F]+) 变化 (\d+) 次")


def parse_offsets(text):
    """解析 --offsets，支持 0x 前缀与逗号/空格分隔。"""
    out = []
    for part in re.split(r"[,\s]+", text or ""):
        part = part.strip()
        if not part:
            continue
        out.append(int(part, 16) if part.lower().startswith("0x") else int(part, 10))
    return out


def extract_candidates(path, min_hits=2, max_n=40):
    """从差分探针日志里提取候选偏移，按「出现次数」降序返回。"""
    hits = {}
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _CAND_RE.search(line)
                if not m:
                    continue
                off = int(m.group(1), 16)
                hits[off] = hits.get(off, 0) + 1
    except OSError:
        return []
    rows = [(off, n) for off, n in hits.items() if n >= min_hits]
    rows.sort(key=lambda r: (-r[1], r[0]))
    return [off for off, _n in rows[:max_n]]


def fmt_val(v):
    return "?" if v is None else str(v)


def diff_offsets(offsets, cur, prev):
    """返回 [(off, old, new)]；prev 为 None 时视为首帧，不算变化。"""
    if prev is None:
        return []
    out = []
    for off in offsets:
        if off not in cur or off not in prev:
            continue
        if prev[off] != cur[off]:
            out.append((off, prev[off], cur[off]))
    return out


def fmt_changes(changes):
    return "  ".join("+0x%06x %s→%s" % (off, fmt_val(a), fmt_val(b))
                     for off, a, b in changes)


def monotonic_state(history):
    """history=[(t, v)]，返回 'only-up' / 'wraps' / 'flat'。用于识别累计计数。"""
    vals = [v for _t, v in history if v is not None]
    if len(vals) < 2:
        return "flat"
    if all(b >= a for a, b in zip(vals, vals[1:])):
        return "only-up"
    return "wraps"


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--offsets", default="", help="逗号分隔，如 0x5bb8a4,0x5e01c0")
    ap.add_argument("--scan-diff", action="store_true", help="从 logs/quest_diff.txt 重新提取候选")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--window", default="8M", help="相对 WorldData 的读取窗口，如 8M")
    ap.add_argument("--baseline", action="store_true", help="只打印一次当前值后退出")
    args = ap.parse_args(argv)

    window = args.window.upper()
    mult = {"K": 1024, "M": 1024 * 1024}.get(window[-1:], 1)
    win = int(window[:-1]) * mult if window[-1:] in "KM" else int(window)
    if win < 0x1000:
        print("窗口太小：%s" % args.window)
        return 2

    offsets = parse_offsets(args.offsets)
    if not offsets and args.scan_diff:
        offsets = extract_candidates(DIFF_LOG)
    if not offsets:
        offsets = list(DEFAULT_OFFSETS)
    offsets = sorted(set(o for o in offsets if 0 <= o < win))

    fh = io.open(CONFIRM_LOG, "a", encoding="utf-8")

    def emit(s=""):
        print(s, flush=True)
        fh.write(s + "\n")
        fh.flush()

    emit("=" * 64)
    emit("任务计数定向确认探针 %s（窗口 %.1fMB，%d 个候选，%.2fs/帧）"
         % (time.strftime("%H:%M:%S"), win / 1048576.0, len(offsets), args.interval))
    emit("候选：" + " ".join("+0x%06x" % o for o in offsets))

    import shadow_warchess_reader as RW

    h = None
    pid = None
    base = None
    prev = None
    t0 = {}
    history = {o: [] for o in offsets}
    n_sample = 0
    frame = 0

    while True:
        try:
            if h is None:
                pid, _p = RW.find_game_pid()
                if not pid:
                    time.sleep(1.0)
                    continue
                h = RW.open_proc(pid)
                try:
                    cache = json.load(io.open(WORLD_CACHE, encoding="utf-8"))
                    base = int(cache[str(pid)]["wd_instance"], 16)
                    emit("[%s] pid=%s WorldData=%#x"
                         % (time.strftime("%H:%M:%S"), pid, base))
                except Exception as exc:
                    emit("[%s] 还没有 WorldData 缓存（%s），先打一局让判定器读一次"
                         % (time.strftime("%H:%M:%S"), exc))
                    base = None
            if base is None:
                time.sleep(1.0)
                continue

            blob = RW.read(h, base, win)
            if not blob or len(blob) < win // 2:
                time.sleep(args.interval)
                continue

            cur = {}
            for off in offsets:
                if off + 4 <= len(blob):
                    cur[off] = struct.unpack_from("<i", blob, off)[0]
            now = time.strftime("%H:%M:%S")
            changes = diff_offsets(offsets, cur, prev)
            n_sample += 1
            frame += 1

            for off in offsets:
                if off in cur:
                    history[off].append((now, cur[off]))

            if args.baseline:
                emit("基线 %s：" % now)
                for off in offsets:
                    if off in cur:
                        emit("  +0x%06x = %d" % (off, cur[off]))
                break

            if changes:
                emit("[%s] #%d 变化  %s" % (now, n_sample, fmt_changes(changes)))
                for off, a, b in changes:
                    state = monotonic_state(history[off])
                    if state == "only-up" and b > a:
                        emit("         ★ +0x%06x 只增不减（%s → %s）" % (off, a, b))
                    elif state == "wraps":
                        emit("         ! +0x%06x 出现回退/环绕（%s → %s）" % (off, a, b))

            prev = cur
            if frame % 200 == 0:
                emit("[%s] 心跳 #%d，当前值：%s"
                     % (now, n_sample,
                        " ".join("+0x%06x=%s" % (o, fmt_val(cur.get(o))) for o in offsets[:8])))
            time.sleep(args.interval)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            emit("[%s] 异常：%s" % (time.strftime("%H:%M:%S"), exc))
            time.sleep(2.0)

    emit("[%s] 汇总（只增不减的偏移 = 最像任务计数）：" % time.strftime("%H:%M:%S"))
    for off in offsets:
        state = monotonic_state(history[off])
        vals = [v for _t, v in history[off] if v is not None]
        if state == "only-up" and len(vals) >= 2:
            emit("  ★ +0x%06x  首=%s 末=%s  样本=%d" % (off, vals[0], vals[-1], len(vals)))
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

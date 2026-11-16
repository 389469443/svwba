#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
给悬浮层喂数据：把监视结果写成 shots/live/overlay.json。

悬浮层（tools/overlay.py）轮询这个文件并显示，所以**读写解耦**：
监视器崩了不影响浮层，浮层关了也不影响监视。

数据字段：
  win/loss/draw  三个概率（识不出局面时为 None，浮层显示 status）
  detail         掉血分布摘要
  note           识别质量提示（例如"N 项低置信度"）

写入方式（2026-10-18 改）
------------------------
**一次写出、原子替换**：先写同目录下进程唯一的临时文件，fsync 落盘，
再 `os.replace` 换上去。读者要么看到完整旧内容，要么看到完整新内容，
不会读到半截 JSON（历史症状：那一场没出数 / 光环面板闪「数据读取失败」）。

三层保护：
  1. 临时文件名带 PID → 两个进程同时写也不会互相踩掉临时文件；
  2. `os.replace` 在 Windows 上可能被"读者正开着目标文件"短暂挡住 → 重试；
  3. 读侧 `read()` / `update()` 自带重试，一次读失败不会直接当成"没数据"。

`update(fields)` 是**只改给定字段**的原子读-改-写：判定器先写战斗结果、
再补 leaderEffects，两步之间面板随时可能来读，所以第二步整个重做一遍替换。

用法（由 battle_monitor.py / watch_mem.py 内部调用，也可手动）：
  python tools/overlay_feed.py --win 62 --loss 35 --draw 3 --detail "..." --note "..."
  python tools/overlay_feed.py --status "等待战斗…"
  python tools/overlay_feed.py --show          # 打印当前内容
"""
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
STATE = os.path.join(PKG, "shots", "live", "overlay.json")

RETRIES = 4               # 读取重试次数
RETRY_DELAY = 0.02        # 每次读取重试间隔（秒）
REPLACE_TRIES = 8         # 原子替换重试次数（Windows：读者开着文件时 replace 会失败）
REPLACE_BASE = 0.01       # 替换重试的起始退避（秒），上限 80ms

_LOCK = threading.Lock()  # 同进程内串行化（判定器是单线程写，留个保险）


def _now():
    return time.strftime("%H:%M:%S")


def write_atomic(path, payload):
    """
    把 payload 原子写到 path。失败抛异常（调用方决定怎么记日志）。

    ⚠️ 不要用 `open(path, "w")` 直接写：那个瞬间文件是空的/半截的，
       面板正好轮询到就会解析失败 —— 这正是「这一场没出数」的一个来源。
    """
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    # 临时名带 PID：同目录下多个写者（判定器 / 工具脚本）不会撞名。
    tmp = "%s.tmp-%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
            fh.flush()
            try:
                os.fsync(fh.fileno())   # 落盘后再替换，避免断电/崩进程留空文件
            except OSError:
                pass                    # 某些文件系统不支持 fsync，不致命
        last = None
        for i in range(max(1, REPLACE_TRIES)):
            try:
                os.replace(tmp, path)   # 原子替换
                return path
            except OSError as exc:
                # Windows：读者正开着目标文件（Python 的 open 不共享删除权限）
                # 时替换会被拒。读者开一次只持续零点几毫秒，退避重试足够。
                last = exc
                time.sleep(min(0.08, REPLACE_BASE * (2 ** i)))
        # 最后手段：原地重写。会有一个极短的"半截"窗口，但读者侧有重试兜底；
        # 比直接丢掉这次更新（面板永远看不到这场结果）要好。
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            return path
        except OSError:
            raise last
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def write(payload):
    """写一份完整 feed（战斗结果 / 阶段切换）。返回写入路径。"""
    p = dict(payload or {})
    p.setdefault("updatedAt", _now())
    with _LOCK:
        return write_atomic(STATE, p)


def read(tries=RETRIES, delay=RETRY_DELAY):
    """
    读 feed；不存在 → None，坏掉 → 重试几次后仍坏 → None。

    旧版一次解析失败就返回 None，调用方当成"没有数据"，
    于是一次瞬时读取失败等于这一场白算。
    """
    tries = max(1, int(tries))
    for i in range(tries):
        try:
            with open(STATE, encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            if i + 1 < tries:
                time.sleep(delay)
    return None


def update(fields, tries=RETRIES * 2, delay=RETRY_DELAY):
    """
    只改 fields 里的键，其它字段原样保留；整体仍是一次原子替换。

    典型用法：战斗结果已经写过了，这里补 `leaderEffects`。
    旧实现是"读出整个文件 → 改一个键 → 用 `open(..., 'w')` 写回"，
    既不是原子写，也会把同时发生的阶段切换覆盖掉。
    """
    fields = dict(fields or {})
    with _LOCK:
        cur = read(tries=tries, delay=delay)
        if not isinstance(cur, dict):
            cur = {}
        cur.update(fields)
        cur.setdefault("updatedAt", _now())
        return write_atomic(STATE, cur)


def main():
    if "--show" in sys.argv:
        print(json.dumps(read(), ensure_ascii=False, indent=1))
        return 0
    payload = {}
    if "--status" in sys.argv:
        payload["status"] = sys.argv[sys.argv.index("--status") + 1]
    for key in ("win", "loss", "draw"):
        if f"--{key}" in sys.argv:
            payload[key] = int(sys.argv[sys.argv.index(f"--{key}") + 1])
    if '--damage-dist' in sys.argv:
        pass  # 分布由 monitor 直接写入 JSON，无需命令行
    for key in ("detail", "note"):
        if f"--{key}" in sys.argv:
            payload[key] = sys.argv[sys.argv.index(f"--{key}") + 1]
    write(payload)
    print(f"-> {STATE}")
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

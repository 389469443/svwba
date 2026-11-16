#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
meta_client / meta_page 离线自测（2026-10-19）

全部用临时目录 + 假 transport，不联网、不开窗口。
验证用户规则：**未连接服务器 → 不收集、不显示统计页**；页面上常驻「数据不足，仅供参考」。
用法： python -u _test_meta_client.py   （exit 0 = 全过）
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import meta_client as MC   # noqa: E402
import meta_page as MP     # noqa: E402

CHECKS = []


def check(name, cond, extra=""):
    CHECKS.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  | " + str(extra)) if extra else ""))
    return bool(cond)


class Tmp:
    """临时目录里的三个文件路径。"""

    def __init__(self):
        self.d = tempfile.mkdtemp()
        self.settings = os.path.join(self.d, "meta-settings.json")
        self.cache = os.path.join(self.d, "meta_cache.json")
        self.queue = os.path.join(self.d, "upload_queue.jsonl")

    def cleanup(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)


def test_gate():
    print("\n== 1. 门控：未连接不收集 == ")
    t = Tmp()
    try:
        check("默认（无配置文件）→ available=False", MC.available(t.settings) is False)
        st = MC.state(t.settings)
        check("原因=未启用", st["reason"] == "未启用", st["reason"])

        cfg = MC.load_settings(t.settings)
        cfg["enabled"] = True
        MC.save_settings(cfg, t.settings)
        st = MC.state(t.settings)
        check("启用但没地址 → False", st["available"] is False)
        check("原因=未填服务器地址", st["reason"] == "未填服务器地址", st["reason"])

        now = 1000.0
        cfg["server"] = "https://example.test"
        cfg["connected"] = True
        cfg["connected_at"] = now
        MC.save_settings(cfg, t.settings)
        check("已连接且新鲜 → True", MC.state(t.settings, now=now)["available"] is True)
        check("连接过期 → False",
              MC.state(t.settings, now=now + MC.CONNECT_TTL + 1)["available"] is False)
        check("过期原因", MC.state(t.settings, now=now + MC.CONNECT_TTL + 1)["reason"]
              == "连接已过期")
    finally:
        t.cleanup()


def test_probe():
    print("\n== 2. 连接探测（假 transport） == ")
    t = Tmp()
    try:
        cfg = MC.load_settings(t.settings)
        cfg["enabled"] = True
        MC.save_settings(cfg, t.settings)
        calls = []

        def ok(url, timeout=5):
            calls.append(url)
            return 200, b'{"ok": true}'

        def bad(url, timeout=5):
            calls.append(url)
            raise OSError("connection refused")

        check("成功探测 → True", MC.probe("https://s.test/", path=t.settings,
                                         transport=ok) is True)
        check("成功写 connected", MC.load_settings(t.settings)["connected"] is True)
        check("请求地址去了尾斜杠", calls[0] == "https://s.test/health", calls[0])
        check("探测后 available=True", MC.available(t.settings) is True)

        check("失败探测 → False", MC.probe("https://s.test", path=t.settings,
                                         transport=bad) is False)
        check("失败写 connected=False",
              MC.load_settings(t.settings)["connected"] is False)
        check("失败后 available=False", MC.available(t.settings) is False)

        def not_ok_body(url, timeout=5):
            return 200, b'{"ok": false}'

        MC.load_settings(t.settings)["enabled"] = True
        check("服务端明确 ok=false → False",
              MC.probe("https://s.test", path=t.settings, transport=not_ok_body) is False)
    finally:
        t.cleanup()


def test_queue():
    print("\n== 3. 上报队列：未连接不入队 == ")
    t = Tmp()
    try:
        row = {"final_board": [{"card_id": 1}], "rank": 1}
        check("未连接 → queue_row 返回 False",
              MC.queue_row(row, path=t.queue, settings_path=t.settings) is False)
        check("未连接 → 不创建队列文件", os.path.isfile(t.queue) is False)

        cfg = MC.load_settings(t.settings)
        cfg.update(enabled=True, server="https://s.test", connected=True,
                   connected_at=9e18)
        MC.save_settings(cfg, t.settings)
        check("已连接 → queue_row 返回 True",
              MC.queue_row(row, path=t.queue, settings_path=t.settings) is True)
        rows = MC.load_queue(t.queue)
        check("队列里有 1 条", len(rows) == 1 and rows[0]["rank"] == 1, rows)
    finally:
        t.cleanup()


def test_render():
    print("\n== 4. 页面渲染 == ")
    r = MP.render(None)
    check("无缓存 → 常驻提示文本", r["note"] == "数据不足，仅供参考")
    check("无缓存 → 暂无数据", "暂无数据" in r["status"], r["status"])

    cache = {"updatedAt": "12:00:00", "server": "https://s.test",
             "stats": {"total": 8, "enough_min": 30,
                       "teams": [{"key": "1|2", "samples": 4, "firsts": 1,
                                  "top4": 4, "avg_rank": 2.5, "first_rate": 0.25,
                                  "top4_rate": 1.0, "enough": False,
                                  "cards": [1, 2]}],
                       "cards": {1: {"appearances": 8, "in_first": 3,
                                     "champion_rate": 0.375}}}}
    r = MP.render(cache)
    check("有缓存 → 提示文本仍在", r["note"] == "数据不足，仅供参考")
    check("状态行带样本数与更新时间",
          "样本 8 局" in r["status"] and "12:00:00" in r["status"], r["status"])
    check("阵容行渲染", len(r["rows"]) == 1 and r["rows"][0]["samples"] == 4)
    check("吃鸡率格式化", r["rows"][0]["first"] == "25.0%", r["rows"][0]["first"])
    check("样本不足标记", r["rows"][0]["enough"] is False)
    check("卡牌行渲染", len(r["cards"]) == 1 and r["cards"][0]["rate"] == "0.38")


def test_open_gate():
    print("\n== 5. 未连接时统计页打不开 == ")
    old = MC.available
    try:
        MC.available = lambda *a, **k: False
        check("open() 返回 None（不创建窗口）", MP.open() is None)
    finally:
        MC.available = old


def main():
    test_gate()
    test_probe()
    test_queue()
    test_render()
    test_open_gate()
    ok = sum(1 for _n, c in CHECKS if c)
    print("\n结果：%d/%d 通过" % (ok, len(CHECKS)))
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())

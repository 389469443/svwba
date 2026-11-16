#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
meta_stats.py 离线自测（2026-10-19）

只测纯函数：归一化 / 签名 / 聚合 / 样本门槛 / 卡牌占比。
不联网、不读真实战绩、不写任何文件（除临时目录）。
用法： python -u _test_meta_stats.py   （exit 0 = 全过）
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import meta_stats as MS  # noqa: E402

CHECKS = []


def check(name, cond, extra=""):
    CHECKS.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  | " + str(extra)) if extra else ""))
    return bool(cond)


def team(ids, ranks, **extra):
    rows = []
    for r in ranks:
        row = {"final_board": [{"card_id": i, "star": 2} for i in ids], "rank": r}
        row.update(extra)
        rows.append(row)
    return rows


def test_normalize():
    print("\n== 1. 归一化 ==")
    out = MS.normalize_board([
        {"card_id": 10, "star": 2, "evolved": True},
        {"cardId": "11", "star": 0},
        {"name": "没有 id", "star": 3},
        {"card_id": 0, "star": 2},
        "不是 dict",
    ])
    check("只保留 card_id>0", [c["card_id"] for c in out] == [10, 11], out)
    check("字符串 cardId 转 int", out[1]["card_id"] == 11)
    check("evolved 布尔化", out[0]["evolved"] is True and out[1]["evolved"] is False)
    check("空输入 → 空列表", MS.normalize_board(None) == [])


def test_signature():
    print("\n== 2. 阵容签名 ==")
    a = [{"card_id": 3, "star": 1}, {"card_id": 1, "star": 5}]
    b = [{"card_id": 1, "star": 2}, {"card_id": 3, "star": 4}]
    check("主 key 忽略星级/顺序一致", MS.signature(a) == MS.signature(b) == "1|3",
          MS.signature(a))
    check("多重集保留重复", MS.signature([{"card_id": 1}, {"card_id": 1},
                                          {"card_id": 2}]) == "1|1|2")
    check("空阵容 → 空串", MS.signature([]) == "")
    check("完整 key 区分星级",
          MS.signature_full([{"card_id": 1, "star": 2}])
          != MS.signature_full([{"card_id": 1, "star": 3}]))


def test_aggregate():
    print("\n== 3. 聚合口径 ==")
    rows = team([10, 11], [1, 2, 3, 4]) + team([10, 12], [1, 1, 5, 6])
    res = MS.aggregate(rows, min_samples=4)
    check("有效局数 = 8", res["total"] == 8, res["total"])
    by_key = {t["key"]: t for t in res["teams"]}
    x = by_key.get("10|11")
    y = by_key.get("10|12")
    check("A 阵容样本 4 / 吃鸡 1", x and x["samples"] == 4 and x["firsts"] == 1, x)
    check("A 前四率 100%", x and x["top4_rate"] == 1.0)
    check("A 平均名次 2.5", x and x["avg_rank"] == 2.5)
    check("B 吃鸡率 50%", y and y["first_rate"] == 0.5, y)
    check("B 前四率 50%", y and y["top4_rate"] == 0.5)
    check("按吃鸡率降序（B 在前）", res["teams"][0]["key"] == "10|12")
    check("样本达标 enough=True", all(t["enough"] for t in res["teams"]))
    check("单卡 10：出现 8 / 冠军 3",
          res["cards"][10]["appearances"] == 8 and res["cards"][10]["in_first"] == 3,
          res["cards"].get(10))
    check("单卡冠军占比 0.375", abs(res["cards"][10]["champion_rate"] - 0.375) < 1e-9)
    check("非法名次被丢弃",
          MS.aggregate([{"final_board": [{"card_id": 1}], "rank": 0},
                        {"final_board": [{"card_id": 1}], "rank": 9}])["total"] == 0)


def test_threshold():
    print("\n== 4. 样本门槛 ==")
    res = MS.aggregate(team([10, 11], [1, 2]), min_samples=30)
    check("2 局 < 30 → enough=False", res["teams"][0]["enough"] is False)
    check("但仍返回数据（只是不达标）", res["teams"][0]["samples"] == 2)


def test_jsonl():
    print("\n== 5. JSONL 读取 ==")
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        with io.open(path, "w", encoding="utf-8") as fh:
            fh.write('{"final_board":[{"card_id":1}],"rank":1}\n')
            fh.write("坏行不是 JSON\n")
            fh.write('{"final_board":[{"card_id":2}],"rank":2}\n')
        rows = MS.load_jsonl(path)
        check("跳过坏行", len(rows) == 2, len(rows))
        check("文件不存在 → 空", MS.load_jsonl(os.path.join(path, "nope")) == [])
    finally:
        os.unlink(path)


def test_history():
    print("\n== 6. 从 history.json 读取 ==")
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump({"games": [
                {"final_board": [{"card_id": 1}], "rank": 1},
                {"board": [{"card_id": 2}], "rank": 3},
                {"board": [], "rank": None},
            ]}, fh)
        rows = MS.load_history_games(path)
        check("空 board / 无名次不丢行（聚合时再过滤）", len(rows) == 3, len(rows))
        check("final_board 与 board 都兼容", rows[1]["final_board"][0]["card_id"] == 2)
        res = MS.aggregate(rows)
        check("聚合后只剩 2 局有效（无名次/空 board 被滤掉）", res["total"] == 2,
              res["total"])
    finally:
        os.unlink(path)


def main():
    test_normalize()
    test_signature()
    test_aggregate()
    test_threshold()
    test_jsonl()
    test_history()
    ok = sum(1 for _n, c in CHECKS if c)
    print("\n结果：%d/%d 通过" % (ok, len(CHECKS)))
    return 0 if ok == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())

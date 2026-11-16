#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自测：overlay.json 原子写 + 界面侧读取重试 + 光环解析失败日志
（对应 HANDOFF §8 的 #6 与 #8）

约束：
  · 只写临时目录，**不碰真实** shots/live/overlay.json、不碰 logs/ 里的真实数据；
  · 不起任何窗口、不读游戏内存、不启动/结束任何进程；
  · 失败退出码 1，全过 0，可重复运行。

用法：
    $PY tools_test_feed_atomic.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, "..", "dsh-shadowverse-huanjing", "tools"))
sys.path.insert(0, HERE)
sys.path.insert(0, TOOLS)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("PASS  " if cond else "FAIL  ") + name + (("  ← " + detail) if (detail and not cond) else ""))
    return bool(cond)


def section(title):
    print("\n── " + title + " " + "─" * max(0, 60 - len(title)))


def read_raw(path):
    with open(path, "rb") as fh:
        return fh.read()


def main():
    import overlay_feed as FEED
    import watch_mem as W

    tmp = tempfile.mkdtemp(prefix="svwb-feed-test-")
    state = os.path.join(tmp, "overlay.json")
    real_state = FEED.STATE
    real_logdir = W.LOGDIR
    FEED.STATE = state
    W.LOGDIR = os.path.join(tmp, "logs")

    try:
        # ── 1. 原子写：内容完整、没有残留临时文件 ────────────────────────
        section("1. overlay_feed.write 原子写")
        FEED.write({"phase": "battle", "win": 42, "loss": 50, "draw": 8,
                    "damageDist": {"toPlayer": [{"damage": 6, "pPercent": 30.0}]}})
        d = json.loads(read_raw(state).decode("utf-8"))
        check("写入后能读回、字段齐全",
              d.get("phase") == "battle" and d.get("win") == 42
              and d.get("damageDist", {}).get("toPlayer"), repr(d))
        check("自带 updatedAt", isinstance(d.get("updatedAt"), str) and len(d["updatedAt"]) >= 8,
              repr(d.get("updatedAt")))
        left = [n for n in os.listdir(tmp) if ".tmp" in n]
        check("目录里没有残留 .tmp 文件", not left, repr(left))
        check("write() 返回的就是 STATE", FEED.write({"win": 1}) == state)

        # ── 2. update()：只改指定字段，其它键原样保留 ────────────────────
        section("2. overlay_feed.update 读-改-写")
        FEED.write({"phase": "operate", "win": None, "gameOver": False,
                    "damageDist": {"toPlayer": [{"damage": 3, "pPercent": 100.0}]}})
        eff = [{"side": "player", "source": "测试来源", "desc": "精灵·随从进场",
                "atk": 1, "hp": 0, "round": 4}]
        FEED.update({"leaderEffects": eff})
        d = json.loads(read_raw(state).decode("utf-8"))
        check("update 后 leaderEffects 生效", d.get("leaderEffects") == eff, repr(d.get("leaderEffects")))
        check("update 保留 phase", d.get("phase") == "operate", repr(d.get("phase")))
        check("update 保留 damageDist", d.get("damageDist", {}).get("toPlayer"), repr(d.get("damageDist")))
        check("update 保留显式 gameOver=False", d.get("gameOver") is False, repr(d.get("gameOver")))
        left = [n for n in os.listdir(tmp) if ".tmp" in n]
        check("update 后也没有残留 .tmp", not left, repr(left))

        # ── 3. 并发读写：读者永远读不到半截 JSON ─────────────────────────
        #
        # ⚠️ 这里**故意**用会重试的读接口（FEED.read / OV.load_state），
        #    不用裸 open()：Windows 上 os.replace 与别人的 open 会短暂互斥，
        #    裸 open 偶发 PermissionError 是系统行为，不是我们的 bug ——
        #    正因为如此，界面侧才必须有重试（HANDOFF §7.4）。
        section("3. 并发：边写边读 600 次（只允许走带重试的读接口）")
        import overlay as OV
        stop = threading.Event()
        bad = []
        reads = {"feed": 0, "state": 0}

        def reader_feed():
            while not stop.is_set():
                try:
                    d = FEED.read()
                    if not isinstance(d, dict):
                        bad.append("FEED.read 返回 %r" % (d,))
                        stop.set()
                        return
                    json.dumps(d)          # 能序列化 = 结构完整
                    reads["feed"] += 1
                    time.sleep(0.002)      # 模拟真实轮询：不是死循环抢占
                except Exception as exc:
                    bad.append("feed: " + repr(exc))
                    stop.set()
                    return

        def reader_state():
            while not stop.is_set():
                try:
                    d = OV.load_state(state)
                    if not isinstance(d, dict):
                        bad.append("load_state 返回 %r" % (d,))
                        stop.set()
                        return
                    reads["state"] += 1
                    time.sleep(0.002)
                except Exception as exc:
                    bad.append("state: " + repr(exc))
                    stop.set()
                    return

        ths = [threading.Thread(target=reader_feed, daemon=True),
               threading.Thread(target=reader_state, daemon=True)]
        for t in ths:
            t.start()
        for i in range(600):
            FEED.write({"phase": "battle", "win": i % 100, "seq": i})
            FEED.update({"leaderEffects": [{"side": "player", "source": "s%d" % i}]})
        stop.set()
        for t in ths:
            t.join(5)
        check("带重试的读接口一次都没失败", not bad, (repr(bad[:3])) or "")
        check("两个读线程都真的读到了东西", reads["feed"] > 0 and reads["state"] > 0, repr(reads))

        # ── 4. read() 重试：坏文件重试、修好后能读 ───────────────────────
        section("4. overlay_feed.read 重试")
        with open(state, "w", encoding="utf-8") as fh:
            fh.write('{"phase": "battle", "win":')     # 半截 JSON
        t0 = time.time()
        check("坏 JSON → read() 返回 None（不抛异常）", FEED.read(tries=2, delay=0.02) is None)
        check("确实等待过重试间隔", time.time() - t0 >= 0.015, "%.3fs" % (time.time() - t0))

        real_load = FEED.json.load
        calls = {"n": 0}

        def flaky_load(fh):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise ValueError("模拟读取失败")
            return real_load(fh)

        FEED.json.load = flaky_load
        try:
            FEED.write({"phase": "ok"})
            got = FEED.read(tries=3, delay=0.01)
        finally:
            FEED.json.load = real_load
        check("前两次读失败、第三次成功", isinstance(got, dict) and got.get("phase") == "ok", repr(got))
        check("重试次数与配置一致（3 次）", calls["n"] == 3, "calls=%d" % calls["n"])

        # ── 5. watch_mem：把光环补进 feed（原子 + 不覆盖其它字段）────────
        section("5. watch_mem._publish_leader_effects_on_feed")
        FEED.write({"phase": "battle", "win": 55, "loss": 40, "draw": 5,
                    "leaderEffects": []})
        W._LEADER_EFFECTS = [
            {"side": "player", "source": "森林骑士道·辛西亚", "desc": "精灵·随从进场",
             "atk": 1, "hp": 0, "round": 3},
            {"side": "opponent", "source": "对方的光环牌", "desc": "精灵·随从进场",
             "atk": 1, "hp": 0, "round": 3},
        ]
        W._publish_leader_effects_on_feed()
        d = json.loads(read_raw(state).decode("utf-8"))
        check("feed 里出现了我方光环", len(d.get("leaderEffects") or []) == 1, repr(d.get("leaderEffects")))
        check("只写我方（对方那条不进去）",
              all(e.get("side") == "player" for e in d.get("leaderEffects") or []),
              repr(d.get("leaderEffects")))
        check("战斗结果字段没被覆盖",
              d.get("win") == 55 and d.get("phase") == "battle", repr((d.get("win"), d.get("phase"))))
        W._LEADER_EFFECTS = []

        # ── 6. 光环解析自检：真实卡库里那 6 张不该报错 ──────────────────
        section("6. 光环解析自检（真实卡库）")
        from shadow_warchess_reader import load_cards
        wlog = W._aura_log_path()
        cards = load_cards()
        n_aura = 0
        bad = []
        for c in cards.values():
            if not isinstance(c, dict):
                continue
            if not any(isinstance(s, dict) and s.get("action") == "claim_aura"
                       for s in (c.get("skills") or [])):
                continue
            n_aura += 1
            desc, atk, hp = W._aura_desc(c)
            ok = W._aura_parse_check(c, W._aura_timing(c), desc, atk, hp)
            if not ok:
                bad.append(c.get("name"))
        check("卡库里能找到带 claim_aura 的牌（>=5）", n_aura >= 5, "n=%d" % n_aura)
        check("已知的这几张全部解析正常（无告警）", not bad, repr(bad))
        check("正常卡不会产生告警日志", not os.path.isfile(wlog), wlog)

        # ── 7. 解析失败必须写日志（§8-8）────────────────────────────────
        section("7. 光环解析失败写日志")
        fake_tribe = {"name": "测试·森之随从", "text":
                      "【<color=Keyword>入场曲</color>】本局游戏中，自己的森之随从进入战场时，使其+1/+1。"}
        check("未知种族文本 → 判定为可疑", W._aura_parse_check(fake_tribe, "when_play", "森之随从进场", 1, 1) is False)
        with io.open(wlog, encoding="utf-8") as fh:
            log1 = fh.read()
        check("日志里有卡名", "测试·森之随从" in log1, log1[:200])
        check("日志原因=条件为空", "条件为空" in log1, log1[:200])
        check("日志带卡面原文片段", "森之随从进入战场" in log1, log1[:200])

        again = W._aura_parse_check(fake_tribe, "when_play", "森之随从进场", 1, 1)
        with io.open(wlog, encoding="utf-8") as fh:
            log2 = fh.read()
        check("同一张牌不会重复刷屏", again is False and log2.count("测试·森之随从") == 1,
              "count=%d" % log2.count("测试·森之随从"))

        fake_num = {"name": "测试·无数值", "text":
                    "【<color=Keyword>谢幕曲</color>】本局游戏中，自己的精灵·随从进入战场时。"}
        W._aura_parse_check(fake_num, "when_destroy", "精灵·随从进场", 0, 0)
        fake_pat = {"name": "测试·没句型", "text":
                    "【<color=Keyword>入场曲</color>】本局游戏中，使其+2/+2。"}
        W._aura_parse_check(fake_pat, "when_play", W._aura_desc(fake_pat)[0], 2, 2)
        with io.open(wlog, encoding="utf-8") as fh:
            log3 = fh.read()
        check("数值缺失会记『数值未解析』", "数值未解析" in log3, log3[-200:])
        check("句型不匹配会记『作用对象未解析』", "作用对象未解析" in log3, log3[-200:])

        # ── 8. 面板侧：半截 JSON 会重试，不会立刻报错 ────────────────────
        section("8. leader_panel._read_snapshot 重试")
        import leader_panel as LP
        p8 = os.path.join(tmp, "lp.json")
        with open(p8, "w", encoding="utf-8") as fh:
            fh.write('{"phase": "battle", "leaderEff')
        raw, snap = LP._read_snapshot(p8, tries=2, delay=0.01)
        check("坏 JSON 重试后仍坏 → kind=error", snap.get("kind") == "error", repr(snap.get("kind")))

        good = json.dumps({"phase": "battle", "leaderEffects": []}).encode("utf-8")
        real_rb = LP._read_bytes
        seq = [b'{"phase": "ba', b'{"phase": "bat', good]

        def flaky_rb(path):
            return seq.pop(0) if seq else good

        LP._read_bytes = flaky_rb
        try:
            raw, snap = LP._read_snapshot(p8, tries=3, delay=0.001)
        finally:
            LP._read_bytes = real_rb
        check("前两次读到半截、第三次成功 → kind=ok", snap.get("kind") == "ok", repr(snap.get("kind")))
        check("成功的快照里 phase 正确", snap.get("phase") == "battle", repr(snap.get("phase")))

        # ── 9. 悬浮层侧：load_state 重试 + 参数化路径 ────────────────────
        section("9. overlay.load_state 重试")
        import overlay as OV
        p9 = os.path.join(tmp, "ov.json")
        with open(p9, "w", encoding="utf-8") as fh:
            fh.write("{坏掉的内容")
        raised = False
        try:
            OV.load_state(p9, tries=2, delay=0.01)
        except Exception:
            raised = True
        check("坏文件（重试后）抛异常给调用方", raised)

        real_load = OV.json.load
        calls2 = {"n": 0}

        def flaky2(fh):
            calls2["n"] += 1
            if calls2["n"] <= 1:
                raise ValueError("模拟读取失败")
            return real_load(fh)

        with open(p9, "w", encoding="utf-8") as fh:
            fh.write('{"phase": "operate", "win": null}')
        OV.json.load = flaky2
        try:
            got = OV.load_state(p9, tries=3, delay=0.01)
        finally:
            OV.json.load = real_load
        check("读失败一次后重试成功", isinstance(got, dict) and got.get("phase") == "operate", repr(got))
        check("重试次数=2（第一次失败+第二次成功）", calls2["n"] == 2, "calls=%d" % calls2["n"])

        # ── 10. live_mode.read_state 重试 ───────────────────────────────
        section("10. live_mode.read_state 重试")
        import live_mode as LM
        p10 = os.path.join(tmp, "lm.json")
        with open(p10, "w", encoding="utf-8") as fh:
            fh.write('{"phase": "battle"')
        raised = False
        try:
            LM.read_state(p10, tries=2, delay=0.01)
        except Exception:
            raised = True
        check("坏文件（重试后）抛异常", raised)
        with open(p10, "w", encoding="utf-8") as fh:
            fh.write('{"phase": "operate", "win": 33}')
        check("好文件正常读出", LM.read_state(p10).get("win") == 33)

        # ── 11. 光环「其他随从死亡计数」只数本侧（2026-10-18 修）───────────
        #
        # 旧 bug：`_seen_names` 是全局 set，而且只记我方 → 算**对方**阿丽雅
        # "其他随从死了几个"时拿的是我方上一轮的阵容，我方少人就让对方光环
        # 误触发（胜率被系统性拉低）。这里设计一个能区分两种实现的局面：
        #   我方：5 张 → 0 张（少 5 个）
        #   对方：阿丽雅 + 4 张普通 → 阿丽雅 + 1 张（少 3 个，还不够 4）
        section("11. 光环死亡计数：按侧分开")
        plain = []
        for c in cards.values():
            if not isinstance(c, dict):
                continue
            if any(isinstance(s, dict) and s.get("action") == "claim_aura"
                   for s in (c.get("skills") or [])):
                continue
            n = c.get("name")
            if n and n not in plain:
                plain.append(n)
        check("卡库里能找到足够多的无光环牌（>=9）", len(plain) >= 9, "n=%d" % len(plain))

        ARIYA = "自然妖精公主·阿丽雅"
        W._PENDING_AURA.clear()
        W._seen_names = {}
        W._AURA_WARNED.clear()

        b1 = {"player": {"level": 3, "board": [{"name": n} for n in plain[:5]]},
              "opponent": {"level": 3,
                           "board": ([{"name": ARIYA}]
                                     + [{"name": n} for n in plain[5:9]])}}
        r1 = W.detect_leader_effects(b1)
        check("第一轮：阿丽雅刚上场、还没人死 → 不发动",
              not [e for e in r1 if e["side"] == "opponent"], repr(len(r1)))

        # 我方 5 张全灭；对方只少 3 张（5 → 阿丽雅 + 1 张普通）
        b2 = {"player": {"level": 4, "board": []},
              "opponent": {"level": 4,
                           "board": [{"name": ARIYA}, {"name": plain[8]}]}}
        r2 = W.detect_leader_effects(b2)
        opp2 = [e for e in r2 if e["side"] == "opponent"]
        check("对方只死 3 个 → 阿丽雅不发动（旧实现会因我方少 5 人而误触发）",
              not opp2, repr([e.get("desc") for e in opp2]))

        # 对方再少 1 张（累计 4）→ 应该发动一次
        b3 = {"player": {"level": 4, "board": []},
              "opponent": {"level": 4, "board": [{"name": ARIYA}]}}
        r3 = W.detect_leader_effects(b3)
        opp3 = [e for e in r3 if e["side"] == "opponent"]
        check("对方累计死满 4 个 → 阿丽雅发动一次", len(opp3) == 1, "n=%d" % len(opp3))
        if opp3:
            check("发动的是「精灵·随从进场」且来源=阿丽雅",
                  "精灵" in str(opp3[0].get("desc"))
                  and opp3[0].get("source") == ARIYA,
                  repr((opp3[0].get("desc"), opp3[0].get("source"))))

        # 同名多张：两张同名随从死一张，只算死 1 个（不能因为 set 去重算成 0）
        W._PENDING_AURA.clear()
        W._seen_names = {}
        dup = plain[0]
        d1 = {"player": {"level": 2, "board": []},
              "opponent": {"level": 2, "board": ([{"name": ARIYA}]
                                                 + [{"name": dup}] * 3)}}
        W.detect_leader_effects(d1)
        d2 = {"player": {"level": 2, "board": []},
              "opponent": {"level": 2, "board": ([{"name": ARIYA}]
                                                 + [{"name": dup}] * 2)}}
        W.detect_leader_effects(d2)
        st = (W._PENDING_AURA.get("opponent") or {}).get(ARIYA) or {}
        check("同名随从少 1 张 → 死亡计数 +1（旧 set 实现会算成 0）",
              int(st.get("deaths") or 0) == 1, repr(st))
        W._PENDING_AURA.clear()
        W._seen_names = {}

    except Exception:
        print("\n[异常] 自测脚本自身出错：")
        traceback.print_exc()
        RESULTS.append(("脚本自身异常", False, ""))
    finally:
        FEED.STATE = real_state
        W.LOGDIR = real_logdir
        W._LEADER_EFFECTS = []
        W._AURA_WARNED.clear()
        shutil.rmtree(tmp, ignore_errors=True)

    ok = sum(1 for _, c, _ in RESULTS if c)
    total = len(RESULTS)
    print("\n" + "=" * 60)
    print("结果：%d/%d 通过" % (ok, total))
    if ok != total:
        for name, c, detail in RESULTS:
            if not c:
                print("  失败：%s %s" % (name, detail))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

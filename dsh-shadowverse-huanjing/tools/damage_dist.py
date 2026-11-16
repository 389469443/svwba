#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
伤害概率分布 —— 用户的核心需求。

需求澄清（用户原话）：
  "我不需要判定主战者的血量，我需要主战者在这次战斗中受到不同伤害的可能性"

所以输出不是"我现在多少血"，而是：
  P(受到 0 点) / P(受到 1 点) / ... / P(受到 N 点)

这正好是蒙特卡洛模拟的直接产物（引擎的 damage.toOpponent.entries）。

伤害公式（用户确认过）：
  伤害 = 胜利方幻境等级 + 残存随从的星级和

用法：
  python tools/damage_dist.py <局面.json> [--sims 20000]
  python tools/damage_dist.py --from-minions <截图>  # 从截图识别后直接算
"""
import io
import json
import os
import shutil
import subprocess
import threading
import time
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ROOT = os.path.dirname(PKG)          # 包根（…\影之幻境助手-v0.1.0）


def _node_path():
    """
    node 解析顺序（能用随包的就先不用系统装的）：

      1. 环境变量 SVWB_NODE / DSH_NODE（调试时手动指定）
      2. 随包 node\\node.exe —— setup_env.ps1 首次启动时下载解压的（免安装、免管理员）
      3. svwb-probe\\node-path.txt —— 安装器写下的绝对路径（装在别处时）
      4. PATH 里的 node —— 系统自己装过

    ⚠️ 这里以前写死过一条 `C:\\Users\\<开发机>\\.dsh\\...\\node.exe`：
       开发机上能跑，换台机器只是噪音（虽然最后会 fallback 到 PATH）。
       已删，改成"随包优先"。
    """
    for key in ("SVWB_NODE", "DSH_NODE"):
        p = os.environ.get(key)
        if p and os.path.isfile(p):
            return p
    cand = os.path.join(ROOT, "svwb-probe", "node", "node.exe")
    if os.path.isfile(cand):
        return cand
    txt = os.path.join(ROOT, "svwb-probe", "node-path.txt")
    try:
        with io.open(txt, "rb") as fh:
            raw = fh.read()
        p = ""
        for enc in ("utf-8-sig", "gbk"):    # 安装器写 UTF-8；旧文件可能是 ANSI
            try:
                p = raw.decode(enc).strip().strip('"')
                break
            except UnicodeDecodeError:
                p = ""
        if p and os.path.isfile(p):
            return p
    except OSError:
        pass
    # 5. 系统常见安装目录（装过 Node 但没进 PATH 的情况）
    for c in (r"C:\Program Files\nodejs\node.exe",
              r"C:\Program Files (x86)\nodejs\node.exe",
              os.path.join(os.environ.get("LOCALAPPDATA") or "", "Programs",
                           "nodejs", "node.exe")):
        if c and os.path.isfile(c):
            return c
    return shutil.which("node") or "node"


NODE = _node_path()


class _Worker:
    """
    常驻 node 模拟器。

    关键收益：省掉「每场都重启 node、重载引擎和知识库」的 ~300ms 固定开销。
    用 stdin/stdout 逐行 JSON 通信，一次启动、反复使用。
    """

    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()

    def _start(self):
        jsf = os.path.join(HERE, "extracted", "sim_worker.mjs")
        if not os.path.isfile(jsf):
            return False
        self.proc = subprocess.Popen(
            [NODE, jsf], cwd=PKG,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        return True

    def _alive(self):
        return self.proc is not None and self.proc.poll() is None

    def call(self, payload, timeout=20.0):
        with self.lock:
            for attempt in (0, 1):
                if not self._alive():
                    # 收尸再拉起
                    try:
                        if self.proc is not None:
                            self.proc.kill()
                    except Exception:
                        pass
                    self.proc = None
                    if not self._start():
                        return None
                try:
                    self.proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
                    self.proc.stdin.flush()
                    line = self.proc.stdout.readline()
                    if not line:
                        raise RuntimeError("worker 无输出")
                    data = json.loads(line)
                    if not data.get("ok"):
                        raise RuntimeError(data.get("error") or "worker 返回失败")
                    return data["result"]
                except Exception:
                    try:
                        if self.proc is not None:
                            self.proc.kill()
                    except Exception:
                        pass
                    self.proc = None
                    if attempt == 1:
                        return None
        return None


_WORKER = _Worker()


def run_sim(battle, sims=1200, seed="dist"):
    """调引擎跑模拟，返回原始结果 JSON"""
    battle = json.loads(json.dumps(battle))
    for side in ("player", "opponent"):
        for f in battle.get(side, {}).get("board", []):
            f.setdefault("star", 1)
    # 优先走常驻 worker（省掉 node 启动开销）
    payload = {"battle": battle, "simulations": sims, "seed": seed}
    got = _WORKER.call(payload)
    if got is not None:
        return got

    # worker 不可用 → 退回一次性 subprocess（保证功能不丢）
    tmp = os.path.join(HERE, "extracted", "_dist_battle.json")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"battle": battle, "simulations": sims, "seed": seed}, fh, ensure_ascii=False)
    # 用 node 内联脚本取完整 JSON（不解析文本输出）
    # 注意：生成的 .mjs 放在 tools/extracted/ 下，所以到 lib/ 要上两级
    js = f"""
import fs from 'node:fs'
const P = await import('../../lib/index.js')
const p = JSON.parse(fs.readFileSync({json.dumps(tmp)}, 'utf8'))
const r = P.toolSimulate(p)
console.log(JSON.stringify({{
  winRate: r.winRatePercent,
  damage: r.damage,
  expected: r.expectedDamage,
  avgSurvivors: r.avgSurvivors,
  avgRounds: r.avgRounds,
  sample: r.sample
}}))
"""
    jsf = os.path.join(HERE, "extracted", "_dist_run.mjs")
    with open(jsf, "w", encoding="utf-8") as fh:
        fh.write(js)
    r = subprocess.run([NODE, jsf], capture_output=True, cwd=PKG,
                       encoding="utf-8", errors="replace", timeout=45)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "")[-800:])
    return json.loads(r.stdout.strip().splitlines()[-1])


# ─────────────────────────────────────────────────────────────────────────────
# 按时间预算跑模拟
# ─────────────────────────────────────────────────────────────────────────────

def _merge(acc, r, n):
    """
    把一批( n 次)结果合并进 acc。

    胜率是百分比，按样本数加权；伤害分布本身是计数，直接相加。
    """
    if not r:
        return acc
    if acc is None:
        acc = {"sims": 0, "win": 0.0, "loss": 0.0, "draw": 0.0,
               "damage": {}, "avgRounds": 0.0, "avgSurvivors": {}}
    acc["sims"] += n
    total = acc["sims"]
    w = r.get("winRate") or {}
    if isinstance(w, dict):
        for key, field in (("player", "win"), ("opponent", "loss"), ("draw", "draw")):
            v = float(w.get(key) or 0)
            acc[field] = (acc[field] * (total - n) + v * n) / total
    # 伤害分布：计数相加
    # ⚠️ 引擎返回的是 {toPlayer: {entries:[{value,count}], denominator, truncated}}，
    #    不是列表。之前按列表遍历导致**一条都没合并进去**，伤害分布永远是空。
    dmg = r.get("damage") or {}
    if isinstance(dmg, dict):
        for side in ("toPlayer", "toOpponent"):
            block = dmg.get(side)
            into = acc["damage"].setdefault(side, {})
            entries = []
            if isinstance(block, dict):
                entries = block.get("entries") or []
            elif isinstance(block, list):
                entries = block
            for e in entries:
                if not isinstance(e, dict):
                    continue
                try:
                    k = int(e.get("value", e.get("damage")))
                    into[k] = into.get(k, 0) + int(e.get("count") or 0)
                except Exception:
                    continue
    # 平均回合数
    try:
        acc["avgRounds"] = ((acc["avgRounds"] * (total - n)) + float(r.get("avgRounds") or 0) * n) / total
    except Exception:
        pass
    # 平均残存
    sv = r.get("avgSurvivors") or {}
    if isinstance(sv, dict):
        for k, v in sv.items():
            try:
                acc["avgSurvivors"][k] = ((acc["avgSurvivors"].get(k, 0.0) * (total - n)) + float(v) * n) / total
            except Exception:
                pass
    return acc


def _finalize(acc):
    """把合并后的计数结构转成和 run_sim 一致的形状"""
    if not acc or acc["sims"] <= 0:
        return {}
    out = {
        "winRatePercent": {"player": round(acc["win"], 2),
                           "opponent": round(acc["loss"], 2),
                           "draw": round(acc["draw"], 2)},
        "winRate": None,
        "avgRounds": round(acc["avgRounds"], 2),
        "avgSurvivors": {k: round(v, 2) for k, v in acc["avgSurvivors"].items()},
        "damage": {},
        "simulations": acc["sims"],
    }
    for side, table in acc["damage"].items():
        total = sum(table.values()) or 1
        entries = []
        cum = 0.0
        for k, c in sorted(table.items()):
            p = c / total
            cum += p
            entries.append({"value": k, "count": c,
                            "probability": round(p, 6),
                            "probabilityPercent": round(p * 100, 2),
                            "cumulative": round(cum, 6)})
        # 保持和引擎一致的形状，下游 dist_rows 才认
        out["damage"][side] = {"entries": entries, "denominator": total,
                               "truncated": False}
    total_w = acc["win"] + acc["loss"] + acc["draw"] or 1
    out["winRate"] = {"player": round(acc["win"] / total_w * 100, 2),
                      "opponent": round(acc["loss"] / total_w * 100, 2),
                      "draw": round(acc["draw"] / total_w * 100, 2)}
    return out


def run_budgeted(battle, budget_ms=300, min_sims=120, max_sims=30000):
    """
    在给定**时间预算**内尽量多跑模拟。

    参数
      budget_ms : 总时间预算（毫秒）。默认 300；有召唤类谢幕曲时传 1000。
      min_sims  : 至少跑多少次（太少百分比没有意义）
      max_sims  : 上限，防止极端情况跑飞

    返回结构与 run_sim 相同（额外带 simulations 字段）。
    """
    import random as _random
    t0 = time.time()
    acc = None
    done = 0
    batch = 150
    seed_base = _random.randint(1, 10 ** 9)

    while done < max_sims:
        spent_ms = (time.time() - t0) * 1000.0
        remain = budget_ms - spent_ms
        # 已经跑够下限、且预算用完 → 收工
        if done >= min_sims and remain <= 5:
            break
        # 保守增量：每批最多翻倍，且上限 400。
        # 实测每场模拟成本会随批量增大而上升（连锁/注册表膨胀），
        # 按线性外推会把预算撑爆（实测 1000ms 预算跑成 16 秒）。
        if done == 0:
            n = min(batch, 150)
        else:
            per_ms = spent_ms / max(1, done)
            est = int(remain / per_ms) if per_ms > 0 else batch
            n = min(est, batch * 2, 400)
            n = max(40, n)
        n = min(n, max_sims - done)
        if n <= 0:
            break
        try:
            r = run_sim(battle, sims=n, seed="b%d" % (seed_base + done))
        except Exception:
            break
        acc = _merge(acc, r, n)
        done += n

    out = _finalize(acc)
    if not out:
        # 一次都没跑成 → 退回固定次数，至少给个结果
        r = run_sim(battle, sims=min_sims)
        try:
            r["simulations"] = min_sims
        except Exception:
            pass
        return r
    return out


def has_summon_lastword(battle, cards_path=None):
    """
    判断场上是否有「谢幕曲会召唤其他随从」的牌
    （不弑的团结者、慈颜的拥趸、腐臭的僵尸、树海的战士、死亡主持人…）。

    这类连锁模拟更贵，用户允许把预算放宽到 1 秒。
    """
    import json as _json
    try:
        if cards_path is None:
            cards_path = os.path.join(PKG, "lib", "knowledge", "cards.json")
        with io.open(cards_path, encoding="utf-8") as fh:
            db = _json.load(fh)
        by_name = {}
        for c in (db.get("cards") or {}).values():
            n = c.get("name")
            if n and n not in by_name:
                by_name[n] = c
        for side in ("player", "opponent"):
            for f in (battle.get(side) or {}).get("board") or []:
                card = by_name.get(f.get("name"))
                if not card:
                    continue
                for s in (card.get("skills") or []):
                    if s.get("timing") != "when_destroy":
                        continue
                    if s.get("action") in ("summon_token", "random_array", "attach_skill"):
                        return True
    except Exception:
        return False
    return False


def dist_table(damage_obj, hp=None, label=""):
    """
    把引擎的 damage 结构转成「伤害 → 概率」表。

    damage_obj: {denominator, entries:[{value, count, probability, probabilityPercent, cumulative}]}
    """
    if not damage_obj or not damage_obj.get("entries"):
        return None
    tot = sum(e["count"] for e in damage_obj["entries"]) or 1
    rows = []
    for e in damage_obj["entries"]:
        rows.append({
            "damage": e["value"],
            "count": e["count"],
            "p": round(e["count"] / tot, 5),
            "pPercent": round(e["count"] / tot * 100, 2),
            "cumulative": e.get("cumulative"),
        })
    rows.sort(key=lambda r: r["damage"])
    # 累计（这是最实用的：P(至少受 N 点)）
    acc = 0.0
    for r in rows[::-1]:
        acc += r["p"]
        r["atLeast"] = round(acc, 5)
    return rows


def format_rows(rows, label, hp=None):
    """人类可读的分布表（含概率条）"""
    if not rows:
        return f"{label}：无数据\n"
    out = [f"{label}（共 {sum(r['count'] for r in rows)} 次样本）"]
    for r in rows:
        bar = "█" * max(0, int(round(r["pPercent"] / 2)))
        line = f"  {r['damage']:>3} 点  {r['pPercent']:>6.2f}%  ≥{r['damage']:+4d}血概率 {r['atLeast']*100:>6.2f}%  {bar}"
        if hp is not None:
            if r["damage"] >= hp:
                line += f"   ← 致死（≥{hp}）"
        out.append(line)
    return "\n".join(out) + "\n"


def compute(battle, sims=1200):
    """返回结构化结果：双方受伤的伤害分布"""
    raw = run_sim(battle, sims)
    win = raw["winRate"]
    dmg_all = raw.get("damage") or {}
    res = {
        "winRate": win,
        "expectedDamage": raw.get("expected"),
        "avgSurvivors": raw.get("avgSurvivors"),
        "avgRounds": raw.get("avgRounds"),
    }
    # 对手掉血 = 我方胜利时
    opp = dist_table(dmg_all.get("toOpponent"))
    # 我方掉血 = 我方失败时
    me = dist_table(dmg_all.get("toPlayer"))
    res["damageToOpponent"] = opp
    res["damageToPlayer"] = me
    return res


def print_result(res, hp_me=None, hp_opp=None):
    w = res["winRate"]
    print(f"\n=== 胜率 {w['player']}%  败率 {w['opponent']}%  平率 {w['draw']}% ===")
    ex = res.get("expectedDamage") or {}
    if ex:
        print(f"（期望掉血：我方 {ex.get('toPlayer', 0):.2f}  对方 {ex.get('toOpponent', 0):.2f}）")
    print("")
    print(format_rows(res["damageToOpponent"], "对方受到的伤害分布", hp_opp))
    print(format_rows(res["damageToPlayer"], "我方受到的伤害分布", hp_me))


def main():
    if "--from-minions" in sys.argv:
        src = sys.argv[sys.argv.index("--from-minions") + 1]
        sys.path.insert(0, HERE)
        import locate_by_samples as LS
        r = LS.run(src)
        if not r["minions"]:
            print("没有识别到随从（这局出现的卡可能还没标注过）")
            return 1
        # 按纵坐标分敌我：上排=对方，下排=我方
        mid = 0.45
        opp = [m for m in r["minions"] if m["box"][1] < mid]
        me = [m for m in r["minions"] if m["box"][1] >= mid]
        battle = {
            "phase": "battle",
            "player": {"hp": 40, "level": 3,
                       "board": [{"name": m["name"], "attack": m["attack"] or 0,
                                  "health": m["health"] or 1, "star": m["star"] or 1} for m in me]},
            "opponent": {"hp": 40, "level": 3,
                         "board": [{"name": m["name"], "attack": m["attack"] or 0,
                                    "health": m["health"] or 1, "star": m["star"] or 1} for m in opp]},
        }
        print(f"识别：我方 {len(me)} 张，对方 {len(opp)} 张")
        for m in me:
            print(f"   我 {m['name']} {m['attack']}/{m['health']} {m['star']}星")
        for m in opp:
            print(f"   敌 {m['name']} {m['attack']}/{m['health']} {m['star']}星")
        res = compute(battle)
        print_result(res)
        return 0

    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
    battle = data.get("battle", data)
    sims = int(sys.argv[sys.argv.index("--sims") + 1]) if "--sims" in sys.argv else 1200
    res = compute(battle, sims)
    print_result(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())

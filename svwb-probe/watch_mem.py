#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开战判定（**纯内存版**）—— 双方等级/血量/场上随从全部来自内存。

为什么这条路现在成立（有实测证据）：
  · `Sv2WarchessBattlePlayerBase` 的布局**已用真值验证**：
        life  = +0x1c   （截图血量 35 → 内存读到 35）
        grade = +0x24   （截图等级 3  → 内存读到 3）
        hero  = +0x18   uid = +0x10
        （+0x20 那 4 字节不是护甲：主战者**没有护甲**属性，2026-10-18 用户确认，见 read_world.py）
  · 2026-10-17 19:33:24 的捕获证明**双方随从都能读到**，
    且与截图完全一致（我方 2 张、对方 1 张 1/1 银白幼龙）

之前读不到不是布局问题，而是**时机**：开战瞬间阵容还没铺开就抢先读了。
本脚本在检测到开战后**等 1.5 秒**再读，读不全就短重试。

流程：开战 → 等 1.5s → 读内存 → 双方随从都非空才算 → 模拟 → 写悬浮层 → 锁定
"""
import argparse
import json
import io
import os
import re
import sys
import time
import threading
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
TOOLS = os.path.join(PKG, "dsh-shadowverse-huanjing", "tools")
sys.path.insert(0, HERE)
sys.path.insert(0, TOOLS)

from shadow_warchess_reader import find_game_pid, open_proc, load_cards  # noqa: E402
import grab as GRAB                                              # noqa: E402
import phase as PHASE                                                   # noqa: E402
import read_world as RW                                                # noqa: E402
import sim_from_memory as SIM                                          # noqa: E402
import overlay_feed as FEED                                            # noqa: E402
import history as HIST                                                  # noqa: E402

LOGDIR = os.path.join(HERE, "logs")


class PhaseTracker:
    """
    画面阶段状态机（去抖 + 边沿输出）。

    update() 每次返回 (edge, 当前确认状态)：
        edge = "rising"   刚确认进入战斗（**一场只触发一次**）
        edge = "falling"  刚确认离开战斗
        edge = None       其余所有采样点

    参数：
        confirm    连续多少次相同才确认切换（默认 2）
        min_dwell  旧状态至少驻留秒数（默认 0.8，防止快速抖动）
    """

    def __init__(self, confirm=2, min_dwell=0.8):
        self.confirm = max(1, int(confirm))
        self.min_dwell = float(min_dwell)
        self.cur = None
        self.cand = None
        self.cand_n = 0
        self.t_enter = 0.0

    def update(self, ph, now):
        if ph == self.cand:
            self.cand_n += 1
        else:
            self.cand = ph
            self.cand_n = 1

        if self.cand_n < self.confirm or self.cand == self.cur:
            return None, self.cur

        # ⚠️ min_dwell 只约束"**离开**战斗"，不能约束"进入战斗"。
        # 之前两边都限制，结果运营阶段采样不足 min_dwell 时，
        # 进入战斗的切换被挡住、候选又被后续采样覆盖 → **整场漏掉**。
        # （这条是测试用例抓出来的，不是推演。）
        if (self.cur == "battle" and self.cand != "battle"
                and (now - self.t_enter) < self.min_dwell):
            return None, self.cur

        prev = self.cur
        self.cur = self.cand
        self.t_enter = now
        if prev != "battle" and self.cur == "battle":
            return "rising", self.cur
        if prev == "battle" and self.cur != "battle":
            return "falling", self.cur
        return None, self.cur

    def force(self, ph, now):
        """强制归位（战斗结束后用）"""
        self.cur = ph
        self.cand = ph
        self.cand_n = self.confirm
        self.t_enter = now




def _start_cache_warmer(h, cards_db, emit, interval=8.0):
    """
    后台线程：持续保证 WorldData 缓存是**当前这一局**的。

    为什么必须放后台：一次完整扫描要 60~78 秒。
    如果放在主循环里，开战那一刻正好在扫，就会错过整场战斗
    （之前就是这么丢掉结果的）。

    返回 (stop_event, thread)
    """
    import threading

    stop = threading.Event()

    _fail_streak = [0]

    def loop():
        while not stop.is_set():
            try:
                # ⚠️ 只在**对局中**才重建缓存。
                #    游戏关掉 / 在主界面时缓存永远失效，以前会每 8 秒做一次
                #    50 秒的 6.4GB 全内存扫描 → 整机卡到无响应
                #    （Windows 事件日志里能看到 python.exe 的 AppHangTransient）。
                if _CUR_PHASE[0] in ("battle", "operate") and not RW.cache_ok(h):
                    t0 = time.time()
                    ok = RW.prewarm(h, cards_db)
                    dt = time.time() - t0
                    if ok:
                        _fail_streak[0] = 0
                        if dt > 2:
                            emit(f"  （后台刷新世界缓存 {dt:.1f}s）")
                    else:
                        # 连续失败 → 指数退避，别把机器拖死
                        _fail_streak[0] += 1
                        back = min(120.0, 8.0 * (2 ** min(4, _fail_streak[0])))
                        stop.wait(back)
                        continue
            except Exception:
                pass
            stop.wait(interval)

    th = threading.Thread(target=loop, name="cache-warmer", daemon=True)
    th.start()
    return stop, th


# ─────────────────────────────────────────────────────────────────────────────
# 主战者光环（只统计我方，跨战斗累积，整局结束才清空）
# ─────────────────────────────────────────────────────────────────────────────
_LEADER_EFFECTS = []          # 累积列表（全局，跨战斗）
_CUR_PHASE = ["operate"]     # 当前阶段（决定主循环睡多久）
# 待核对的一场：{"hp_before":..., "dist":[...], "win":..., "t":...}
_CALIB = [None]
# 上一轮场上出现过的随从名字：**按侧分开**，而且用 Counter（同名多张要分别计数）。
# ⚠️ 这里必须记**场上所有随从**，不只是带光环的那几张：
#    「其他随从被破坏 N 个后发动」数的是场上其它随从；
#    旧实现把记名放在 `if not aura_skills: continue` 之后 → 只记了带光环的牌，
#    普通随从死了完全不算数（阿丽雅基本永远凑不够 4 个）。
# ⚠️ 而且旧实现是一个全局 set、只记我方 → 算对方时拿我方的阵容去比，会误触发。
#    两处都是 2026-10-18 修的（见 §14.6）。
_seen_names = {}
# 谢幕曲光环的"待生效"记账：side -> {卡名: 获得回合}
_PENDING_AURA = {}


def _cards_by_name():
    """缓存「卡名 → 卡数据」映射（只读一次）"""
    global _CARDS_BY_NAME
    try:
        return _CARDS_BY_NAME
    except NameError:
        pass
    import json as _json
    _CARDS_BY_NAME = {}
    try:
        p = os.path.join(os.path.dirname(HERE), "dsh-shadowverse-huanjing",
                         "lib", "knowledge", "cards.json")
        with io.open(p, encoding="utf-8") as fh:
            db = _json.load(fh)
        for c in (db.get("cards") or {}).values():
            n = c.get("name")
            if n and n not in _CARDS_BY_NAME:
                _CARDS_BY_NAME[n] = c
    except Exception:
        _CARDS_BY_NAME = {}
    return _CARDS_BY_NAME


def _parse_opt(opt):
    """把 "base_card_id=60000020&save_after_battle=1" 解析成 dict"""
    if isinstance(opt, dict):
        return opt
    out = {}
    for part in str(opt or "").split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _card_text(card):
    """卡面文本（去掉 <color=Keyword> 之类的标签）；没有就是空串。"""
    t = card.get("text")
    txt = t if isinstance(t, str) else ""
    if not txt and isinstance(t, dict):
        txt = t.get("normal") or ""
    return re.sub(r"<[^>]+>", "", txt or "")


def _aura_desc(card):
    """
    从卡面文本里解析光环效果。

    目标格式：「...某类随从进入战场时，使其 +N/+M...」
    返回 (desc, add_atk, add_hp)：
        desc   —— 简短描述，例如 "精灵·随从进场"（数值单独存，不带进 desc）
        add_atk/add_hp —— 解析出的数值（解析不到就是 0）

    ⚠️ 解析失败**不在这里报**（本函数要保持纯函数、可被反复调用），
       由 `_aura_parse_check()` 在真正生成光环条目时统一写日志。
    """
    txt = _card_text(card)

    # 抓 +N/+M（也兼容「+1/+1」之外的写法）
    m = re.search(r"\+(\d+)/\+(\d+)", txt)
    atk = int(m.group(1)) if m else 0
    hp = int(m.group(2)) if m else 0

    # 抓作用对象：「自己的<前面那一段>进入战场」
    who = ""
    m2 = re.search(r"自己的(.{0,14}?)进入战场", txt)
    if m2:
        who = m2.group(1).strip("，,。 ")
    # 去掉「本局游戏中，」这类前缀噪音
    who = who.replace("本局游戏中", "").strip("，,。 ")
    if not who:
        who = "随从"
    # ⚠️ desc **不带数值**：数值单独存。
    #    这样「虫风花 +1/+0」「阿丽雅 +2/+0」会归成同一类（精灵·随从进场），
    #    数值累加成 +3/+0 —— 用户要求「同种光环合并到一类」。
    return f"{who}进场", atk, hp


# 本局状态：防止重复记录、控制清空时机
_GAME_STATE = {
    "recorded": False,      # 本局是否已经写过战绩
    "hero": None,           # 本局的主战者（换人就当新一局）
    "over": False,          # 本局是否已结束（血量 <= 0）
}


def _reset_game_state():
    _GAME_STATE["recorded"] = False
    _GAME_STATE["over"] = False
    _GAME_STATE["rank"] = None          # 新一局：名次重新计
    try:
        _seen_names.clear()
        _PENDING_AURA.clear()
    except Exception:
        pass


# 名次补读节流：`_sync_rank` 与 `_backfill_rank` 共用同一个时间戳，
# 避免同一轮各扫一遍内存（一次快照约 30s，重复扫会把判定器拖死）。
_RANK_RETRY_AT = [0.0]


def _rank_retry_due(gap=8.0):
    now = time.time()
    if now - _RANK_RETRY_AT[0] < gap:
        return False
    _RANK_RETRY_AT[0] = now
    return True


def _sync_rank(emit):
    """
    结算名次补写 —— **同一局只写一次，且只在出局后采信**。

    旧实现每 1.5 秒用当前名次覆盖战绩最后一条：
      · 对局中的名次表还没定稿（同一个 uid 有多张快照），会把第 4 名写成
        第 3/2 名（用户实机：结算画面第 4 名，history.json 记成 rank=3）；
      · 结算后还会被后续读数反复改写。
    现在：最后一条战绩**已结算（hp 有值）但还没有名次**时才补读一次；
    写入后 rank 非空，后续调用直接返回 —— 一局只写一次、不会被 None 或
    后面的旧快照覆盖。
    """
    try:
        import history as _H
        games = _H._load().get("games") or []
        last = games[-1] if games else None
        if not isinstance(last, dict):
            return
        if last.get("rank") is not None:
            return                      # 本局名次已经写过 → 不再改
        if last.get("hp") is None:
            return                      # 还没结算 → 名次未定稿，不采信
        if int(last.get("hp") or 0) > 0:
            return                      # 没出局（正常不会走到）
        if not _rank_retry_due():
            return
        import read_rank as RR
        from shadow_warchess_reader import find_game_pid, open_proc, load_cards
        pid, _p = find_game_pid()
        if not pid:
            return
        hh = open_proc(pid)
        r = RR.snapshot(hh, load_cards()) or {}
        mine = r.get("mine") or {}
        rank = mine.get("rank")
        # 只采信「我方确实出局（life<=0）」且表里读到的名次
        if not rank or int(mine.get("life") or 0) > 0:
            return
        if not _H.update_game(len(games) - 1, rank=int(rank)):
            return
        _GAME_STATE["rank"] = int(rank)
        emit("  ☆ 补上名次：第%s名（本局只写一次）" % int(rank))
    except Exception:
        pass


def _backfill_rank(emit):
    """
    出局时名次表可能还没定稿 → 之后每轮试着补读，读到就回填。

    用户说明：判名次要等血量真的低于 0 之后才准。
    这里也走 `_rank_retry_due()` 节流（与 `_sync_rank` 共用），
    且只在最后一条**还没有名次**时读：写入后本函数不再扫描内存。
    """
    try:
        import history as _H
        games = _H._load().get("games") or []
        if not games:
            return
        last = games[-1]
        if not isinstance(last, dict):
            return
        if last.get("rank") is not None:
            return
        hp = last.get("hp")
        if hp is None or int(hp) > 0:
            return
        if not _rank_retry_due():
            return
        import read_rank as RR
        import read_world as _RW
        from shadow_warchess_reader import find_game_pid, open_proc, load_cards
        pid, _ = find_game_pid()
        if not pid:
            return
        _RW.WD_PID[0] = pid
        hh = open_proc(pid)
        r = RR.snapshot(hh, load_cards())
        mine = (r or {}).get("mine")
        if mine and int(mine.get("life") or 0) <= 0 and mine.get("rank"):
            _H.update_game(len(games) - 1, rank=int(mine["rank"]))
            emit("  ☆ 补上名次：第%s名" % mine["rank"])
    except Exception:
        pass


def _maybe_record_game_over(hero_id, hp, board, emit):
    """
    记录整局结束 —— **每一局只记一次**。

    返回 True 表示这次真的写了新记录。

    ⚠️ 去重不能再用「(主战者, 最终血量)」指纹：
       连打两局都掉到 -10 时会被当成同一局，第二局就**永远不记录**
       （用户实测：打了好几局，战绩一直只有一局）。
       正确判据是**历史里最后一条是否已结算**：
         · 最后一条还没有 hp（未结算）→ 这就是本局，结算它
         · 最后一条已经有 hp（已结算）→ 本局已经记过，直接跳过；
           等下一局开始（血量回到 >0）由 ensure_game 新建一条
    """
    try:
        import history as _H
        _games = _H._load().get("games") or []
        _last = _games[-1] if _games else None
    except Exception:
        _games, _last = [], None

    if _last is None:
        # 一条记录都没有 → 说明这一局从来没"活着"过（插件启动时就已是死亡局面），
        # 不该凭空写一条战绩。
        _GAME_STATE["recorded"] = True
        _GAME_STATE["hero"] = hero_id
        _GAME_STATE["over"] = True
        emit("  （没有进行中的对局记录，跳过写入）")
        return False
    if _last.get("hp") is not None:
        # 最后一条已结算 → 这一局已经记过，不重复写
        _GAME_STATE["recorded"] = True
        _GAME_STATE["hero"] = hero_id
        _GAME_STATE["over"] = True
        emit("  （这一局已经记录过，跳过重复写入）")
        return False

    _GAME_STATE["recorded"] = True
    _GAME_STATE["hero"] = hero_id
    _GAME_STATE["over"] = True
    # 用户要求：这一把结束后**清空光环面板**（面板随之隐藏），
    # 只有下次真正获得光环时才重新出现并记录。
    try:
        clear_leader_effects()
        emit("  （本局结束，光环面板已清空）")
    except Exception:
        pass
    rank = None
    _PREV_RANK = _GAME_STATE.get("last_rank")
    try:
        import read_rank as RR
        import read_world as _RW
        from shadow_warchess_reader import find_game_pid, open_proc, load_cards
        pid, _ = find_game_pid()
        if pid:
            _RW.WD_PID[0] = pid
            hh = open_proc(pid)
            r = RR.snapshot(hh, load_cards())
            mine = (r or {}).get("mine")
            # 只在我们已经出局（生命<=0）时采信名次表；
            # 否则那张表可能还在更新中，读到的名次会偏（实测把第3名记成了第2名）。
            if mine and int(mine.get("life") or 0) <= 0:
                rank = mine.get("rank")
    except Exception:
        rank = None
    # 结算画面/离开对局后名次表常常读不到 → 用死前最后一次读到的名次兜底
    if not rank and _PREV_RANK:
        rank = _PREV_RANK
    # 回填到「选好主战者时先建的那条」，避免同一局出现两条
    HIST.finish_game(hero_id, rank, hp, board or [], note="血量归零，整局结束")
    # 旧版这里还会写 (hero, hp) 指纹做去重；现在改为"看最后一条是否已结算"，
    # 指纹文件不再需要，顺手清掉。
    try:
        _sig_path = os.path.join(HERE, "logs", "_last_game_record.json")
        if os.path.isfile(_sig_path):
            os.remove(_sig_path)
    except Exception:
        pass
    emit(f"  ★ 整局结束：名次={rank or '?'}  最终血量={hp}")
    return True


def _maybe_new_game(hero_id, hp, emit, grade=None):
    """
    检测"新一局开始"：
      · 血量回到 > 0，且本局之前已经结束（over=True）→ 新一局
      · 主战者换了 → 新一局
    新一局开始时才**清空主战者效果面板**（用户要求：等下一把开始才清）。
    """
    prev_hero = _GAME_STATE["hero"]
    hero_changed = prev_hero is not None and hero_id is not None and hero_id != prev_hero
    revived = _GAME_STATE["over"] and int(hp or 0) > 0
    # 等级明显掉回去（>=3 级 → <=2 级）也说明开了一把新的：
    # 上一局结束后判定器可能没看到"出局"，只靠 hero 变化会漏（用户反馈带了上一局状态）。
    lv_drop = False
    try:
        _prev_lv = _GAME_STATE.get("grade")
        _now_lv = int(grade) if grade is not None else None
        if _prev_lv is not None and _now_lv is not None:
            lv_drop = (_prev_lv >= 3 and _now_lv <= 2)
        if _now_lv is not None:
            _GAME_STATE["grade"] = _now_lv
    except Exception:
        pass
    if hero_changed or revived or lv_drop:
        _reset_game_state()
        clear_leader_effects()
        _reset_battle_stats()          # 新一局：徒姬 X / 维尔伯特 X / 进化次数归零
        try:
            _feed_write({"phase": "other", "win": None, "newGame": True,
                        "gameOver": False, "leaderEffects": [],
                        "updatedAt": time.strftime("%H:%M:%S")})
        except Exception:
            pass
        emit("  ☆ 新一局开始：清空主战者效果")
        return True
    return False


def leader_effects_for_feed():
    """
    给面板用的光环列表：**只取我方**，并且保留**逐来源**的原始条目。

    为什么不在这里合并：
      面板要按「回合 → 同类型效果 → 来源」三层展示，
      提前合并会把回合和来源压掉，面板就没法分组了。
      合并交给面板自己做。
    """
    out = []
    for e in _LEADER_EFFECTS:
        if (e.get("side") or "player") != "player":
            continue
        out.append(e)
    return out

# ─────────────────────────────────────────────────────────────────────────────
# 战斗统计（2026-10-18）：徒姬的 X、维尔伯特的 X、本局进化次数
#
# 徒姬（超进化）：本局**战斗阶段**进入战场的梦魇随从数
# 维尔伯特（超进化）：本局被破坏的守护随从数
# 进化次数：我方随从形态发生变化的次数（吃点进化 / 效果进化都算）
#
# 都是**轮询观测**（判定器每 150ms 读一次），极短的"进场即死"可能漏计 ——
# 和光环死亡计数同样的口径，宁少不多。
# ─────────────────────────────────────────────────────────────────────────────
_BSTAT = {"nightmare_in": 0, "guard_dead": 0, "evolved": 0}
# 是否处于战斗中（含开战瞬间的过渡窗口）：梦魇进场只统计战斗阶段，
# 但计数**整场游戏累积**（用户 2026-10-18 明确：不是单场战斗）。
_BATTLE_WINDOW = [False]
_EMIT = [None]
_EV_SAVED = [0]                  # 已落盘的进化数（避免每轮都写盘）
_CNT_SAVED = [None]              # 已落盘的守护/梦魇计数（None=还没写过）
_BSNAP = {"objs": {}}
_LAST_MY = {"cards": []}


def _counters_store_path():
    return os.path.join(LOGDIR, "_battle_counters.json")


def _counters_save():
    """守护/梦魇计数落盘（重启判定器不丢，用户 2026-10-18 要求）"""
    try:
        FEED.write_atomic(_counters_store_path(),
                          {"guard_dead": int(_BSTAT.get("guard_dead") or 0),
                           "nightmare_in": int(_BSTAT.get("nightmare_in") or 0),
                           "savedAt": time.time()})
    except Exception:
        pass


def _counters_load():
    try:
        p = _counters_store_path()
        if not os.path.isfile(p):
            return
        if time.time() - os.path.getmtime(p) > 600:
            return
        with io.open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        _BSTAT["guard_dead"] = max(0, min(999, int(d.get("guard_dead") or 0)))
        _BSTAT["nightmare_in"] = max(0, min(999, int(d.get("nightmare_in") or 0)))
    except Exception:
        pass


def _evolve_store_path():
    return os.path.join(LOGDIR, "_evolve_count.json")


def _evolve_save():
    """把本局进化数落盘（判定器重启后要保留，用户 2026-10-18 要求）"""
    try:
        FEED.write_atomic(_evolve_store_path(),
                          {"evolved": int(_BSTAT.get("evolved") or 0),
                           "ids": sorted(str(x) for x in
                                         (_BSTAT.get("evolved_ids") or set())),
                           "savedAt": time.time()})
    except Exception:
        pass


def _evolve_load():
    """启动时把进化数读回来；超过 10 分钟按上一局处理，不沿用。"""
    try:
        p = _evolve_store_path()
        if not os.path.isfile(p):
            return
        if time.time() - os.path.getmtime(p) > 600:
            return
        with io.open(p, encoding="utf-8") as fh:
            d = json.load(fh)
        n = int(d.get("evolved") or 0)
        if 0 < n <= 30:
            _BSTAT["evolved"] = n
            _BSTAT["evolved_ids"] = set(d.get("ids") or [])
    except Exception:
        pass


def _reset_battle_stats():
    _BSTAT.update(nightmare_in=0, guard_dead=0, evolved=0, evolved_ids=set())
    for _p in (_evolve_store_path(), _counters_store_path()):
        try:
            if os.path.isfile(_p):
                os.remove(_p)
        except Exception:
            pass
    _BSNAP["objs"] = {}
    _LAST_MY["cards"] = []


def _cards_by_id():
    """卡号 → 卡数据（含 cardIds 里所有形态）"""
    global _CARDS_BY_ID
    try:
        return _CARDS_BY_ID
    except NameError:
        pass
    db = {}
    for _name, c in _cards_by_name().items():
        try:
            db[int(c.get("baseCardId") or 0)] = c
        except Exception:
            pass
        for cid in (c.get("cardIds") or []):
            try:
                db[int(cid)] = c
            except Exception:
                pass
    _CARDS_BY_ID = db
    return db


def _form_of(card, cid):
    """形态：0=基础，1=进化，2=超进化（按 cardIds 的位置）"""
    try:
        base = int(card.get("baseCardId") or 0)
        ids = [int(x) for x in (card.get("cardIds") or [])]
        if cid and base and cid != base and cid in ids:
            return max(1, ids.index(cid))
    except Exception:
        pass
    return 0


def _is_guard(card):
    for sk in (card.get("skills") or []):
        if isinstance(sk, dict) and sk.get("action") == "guard":
            return True
    return False


def _obj_key(c):
    o = c.get("obj") or ""
    if o:
        return o
    return "%s|%s" % (c.get("name"), c.get("card_id"))


def _battle_stats_update(snap, h=None):
    """
    每次读内存后调用：观测我方场上变化，累计三个统计量。
    只统计**我方**（用户要求：面板只显示我方）。
    """
    try:
        me = next((p for p in ((snap or {}).get("players") or [])
                   if p.get("side") == "我方"), None)
        if not me:
            return
        cards = me.get("cards") or []
        _LAST_MY["cards"] = cards
        if h is not None:
            try:
                _apply_text_stats(h)
            except Exception:
                pass
            try:
                _cl = _claimed_auras(h, me)
                if _cl:
                    merge_leader_effects(_cl)
            except Exception:
                pass
        # 进化次数**不看进化点**（用户 2026-10-18 明确）：
        # 改成看每张随从**自身的进化状态** —— 形态 >= 1 就算"进化过"，
        # 以 uid（拿不到就用 obj）去重，所以"判定器启动前就在场上的进化随从"也能算到。
        # 同一只随从只记一次（一只随从不会进化两次）。
        try:
            _ids = _BSTAT.setdefault("evolved_ids", set())
            for c in cards:
                try:
                    _form = int(c.get("__form") or 0)
                except Exception:
                    _form = 0
                if _form >= 1:
                    _ids.add(c.get("uid") or c.get("obj") or c.get("name"))
            _BSTAT["evolved"] = len(_ids)
        except Exception:
            pass
        byid = _cards_by_id()
        cur = {}
        for c in cards:
            try:
                cid = int(c.get("card_id") or 0)
            except Exception:
                cid = 0
            card = byid.get(cid) or {}
            # 形态优先用实例上读到进化状态（0 基础 / 1 进化 / 2 超进化 / 3 三连）；
            # 读不到（偏移未校准）就退回按 card_id 在 cardIds 里的位置判断。
            # 形态：**以卡号为准**（card_id 在 cardIds 里的位置）；
            # 只有卡号还是基础卡、但对象上标记了已进化时，才算 1（进化）。
            # ev_st 的编码与卡号对不上（实测辛西亚 is_ev=1/ev_st=2 但面板是进化值），
            # 不能拿它当唯一依据。
            form = _form_of(card, cid)
            try:
                if form == 0 and int(c.get("is_evolve") or 0) == 1:
                    form = 1
            except Exception:
                pass
            try:
                c["__form"] = form       # 给上面的"进化计数"用
            except Exception:
                pass
            key = _obj_key(c)
            try:
                _atk = int(c.get("atk") or 0)
                _hp = int(c.get("life") or 0)
            except Exception:
                _atk, _hp = 0, 0
            cur[key] = {"form": form,
                        "tribe": card.get("tribe"),
                        "guard": _is_guard(card),
                        "atk": _atk, "hp": _hp, "cid": cid}
            old = _BSNAP["objs"].get(key)
            if old is None:
                # 新出现的随从：战斗阶段出现、且是梦魇 → 徒姬的 X +1
                if (_BATTLE_WINDOW[0] or _CUR_PHASE[0] == "battle") \
                        and card.get("tribe") == "梦魇":
                    _BSTAT["nightmare_in"] = int(_BSTAT.get("nightmare_in") or 0) + 1
                    try:
                        if _EMIT[0]:
                            _EMIT[0]("  ★ 梦魇进场：%s（本局累计 %d）"
                                     % (c.get("name"), _BSTAT["nightmare_in"]))
                    except Exception:
                        pass
                # 战斗阶段"一出现就是进化形态"（召唤即进化 / 两次轮询之间进化完）
                # 也要算进本局进化数（用户反馈战斗中进化没计入）。
                if form >= 1 or int(c.get("is_evolve") or 0) == 1:
                    _BSTAT.setdefault("evolved_ids", set()).add(key)
            else:
                # 进化判定（2026-10-18 用户要求"别只看进化点"）：
                #   ① 形态档次上升；
                #   ② 卡号变成同族的进化卡号；
                #   ③ 面板正好 +2/+2 或 +3/+3（进化的固定加成）；
                #   任一成立且这只随从没记过 → 进化次数 +1
                _ev = False
                if form > old.get("form", 0):
                    _ev = True
                _cid_old, _cid_new = old.get("cid"), cid
                if not _ev and _cid_old and _cid_new and _cid_old != _cid_new:
                    _c = byid.get(_cid_new) or {}
                    _ids = [int(x) for x in (_c.get("cardIds") or [])]
                    if _cid_old in _ids and _ids.index(_cid_new) > _ids.index(_cid_old):
                        _ev = True
                if not _ev:
                    _da = _atk - int(old.get("atk") or 0)
                    _dh = _hp - int(old.get("hp") or 0)
                    if (_da, _dh) in ((2, 2), (3, 3)):
                        _ev = True
                if _ev:
                    _BSTAT.setdefault("evolved_ids", set()).add(key)
        for key, old in _BSNAP["objs"].items():
            if key in cur:
                continue
            # 战斗阶段消失 = 被破坏（维尔伯特的 X）；卖了/换下去的不算
            if old.get("guard") and _CUR_PHASE[0] == "battle":
                _BSTAT["guard_dead"] = int(_BSTAT.get("guard_dead") or 0) + 1
                try:
                    if _EMIT[0]:
                        _EMIT[0]("  ★ 守护随从被破坏（本局累计 %d）"
                                 % _BSTAT["guard_dead"])
                except Exception:
                    pass
        _BSNAP["objs"] = cur
        try:
            _BSTAT["evolved"] = len(_BSTAT.get("evolved_ids") or set())
            # 贝鲁（煌击战士·贝鲁）的卡面 X = 本局进化次数，直接读它的 +0x34。
            # 实测：贝鲁 +0x34=5 时，我们统计的进化数也是 5 → 用它做权威值。
            for _c in _LAST_MY["cards"]:
                _nm = str(_c.get("name") or "")
                _x = int(_c.get("evolve_value") or 0)
                if "贝鲁" in _nm:
                    if 0 < _x <= 60:
                        _BSTAT["evolved"] = max(int(_BSTAT["evolved"]), _x)
                elif "维尔伯特" in _nm:
                    # 维尔伯特的 X = 本局被破坏的守护随从数（直接读卡对象）
                    if 0 < _x <= 99:
                        _BSTAT["guard_dead"] = max(int(_BSTAT["guard_dead"]), _x)
                elif "徒姬" in _nm:
                    # 徒姬的 X = 本局战斗阶段进场的梦魇数
                    if 0 < _x <= 99:
                        _BSTAT["nightmare_in"] = max(int(_BSTAT["nightmare_in"]), _x)
        except Exception:
            pass
        _n_ev = int(_BSTAT.get("evolved") or 0)
        if _n_ev != _EV_SAVED[0]:
            _EV_SAVED[0] = _n_ev
            _evolve_save()
        _cnt = (int(_BSTAT.get("guard_dead") or 0),
                int(_BSTAT.get("nightmare_in") or 0))
        if _cnt != _CNT_SAVED[0]:
            _CNT_SAVED[0] = _cnt
            _counters_save()
    except Exception:
        pass


def _special_effects_for_feed():
    """
    其他随从效果：**只在我方场上真的出现这张牌时才有条目**（用户要求）。

    展示口径（用户 2026-10-18）：这类"带任务的 X"不要写成 +X/+X，
    直接写「条件 + X 的值」，例如：
        本局战斗阶段进场的梦魇数    3        ← 傍死的安纳提玛·徒姬（超进化）
        本局被破坏的守护随从数      1        ← 呜咽的圣骑士·维尔伯特（超进化）
    基础/进化形态没有 X，就直接给固定数值（+6/+6、+12/+12、+1/+2、+2/+4）。
    """
    out = []
    # 用户 2026-10-18：这两个计数**不管牌在不在场都要显示**（它们在为徒姬/维尔伯特攒 X）
    try:
        if int(_BSTAT.get("guard_dead") or 0) > 0:
            out.append({"desc": "本局被破坏的守护随从数量",
                        "value": str(int(_BSTAT["guard_dead"]))})
        if int(_BSTAT.get("nightmare_in") or 0) > 0:
            out.append({"desc": "本局战斗梦魇随从进场数",
                        "value": str(int(_BSTAT["nightmare_in"]))})
    except Exception:
        pass
    try:
        byid = _cards_by_id()
        for c in _LAST_MY["cards"]:
            name = str(c.get("name") or "")
            try:
                cid = int(c.get("card_id") or 0)
            except Exception:
                cid = 0
            card = byid.get(cid) or {}
            form = _form_of(card, cid)
            try:
                ev_st = c.get("evolve_state")
                if isinstance(ev_st, int) and 0 <= ev_st <= 3:
                    if ev_st > 0 or int(c.get("is_evolve") or 0) == 1:
                        form = max(form, ev_st if ev_st > 0 else 1)
            except Exception:
                pass
            if "徒姬" in name:
                if form >= 2:
                    out.append({"desc": "本局战斗梦魇随从进场数",
                                "value": str(int(_BSTAT["nightmare_in"])),
                                "source": name + "（超进化）"})
                else:
                    v = "+12/+12" if form == 1 else "+6/+6"
                    out.append({"desc": "梦魇·随从进场", "value": v,
                                "source": name + ("（进化）" if form == 1 else "")})
            elif "维尔伯特" in name:
                if form >= 2:
                    out.append({"desc": "本局游戏被破坏的守护随从数量",
                                "value": str(int(_BSTAT["guard_dead"])),
                                "source": name + "（超进化）"})
                else:
                    v = "+2/+4" if form == 1 else "+1/+2"
                    out.append({"desc": "守护·随从面板", "value": v,
                                "source": name + ("（进化）" if form == 1 else "")})
    except Exception:
        pass
    return out


_SPECIAL_AURA_MAP = [None]

# ── 从**卡面文本**里直接读游戏内数值（2026-10-18）─────────────────────────
# 游戏会把条件值填进卡面文本：`...数为<color=Keyword>3</color>` / `（X为<color=Keyword>3</color>）`。
# 第一次启动扫一次内存（约 9 秒/模式，后台线程），记住地址；之后每轮直接读那几段字符串。
_TEXT_STATS = {"guard": [], "x": [], "scan_at": 0.0}
_TEXT_SCAN_LOCK = threading.Lock()


def _parse_text_value(text):
    m = re.search(r"数为<color=Keyword>(\d+)</color>", text)
    if m:
        return int(m.group(1))
    m = re.search(r"（X为<color=Keyword>(\d+)</color>）", text)
    if m:
        return int(m.group(1))
    return None


def _scan_text_stats(emit=None):
    """扫一次内存，找"已填值"的卡面文本地址（带上下文以区分 X 的含义）。"""
    with _TEXT_SCAN_LOCK:
        try:
            pid, _p = find_game_pid()
            if not pid:
                return
            hh = open_proc(pid)
            guard_addrs, x_addrs = [], []
            for pat, bucket in (("被破坏的守护随从数为", "guard"),
                                ("（X为", "x")):
                try:
                    hits = RW.scan_bytes(hh, pat.encode("utf-16-le"), limit=24)
                except Exception:
                    continue
                for a in hits:
                    blob = RW.read(hh, a, 0x700)
                    if not blob:
                        continue
                    try:
                        s = blob.decode("utf-16-le", "replace")
                    except Exception:
                        continue
                    v = _parse_text_value(s)
                    if v is None:
                        continue          # 模板（%d）不算
                    if bucket == "guard":
                        guard_addrs.append(a)
                    else:
                        ctx = "梦魇" if "梦魇" in s else ("进化" if "进化" in s else "")
                        x_addrs.append((a, ctx))
            if guard_addrs or x_addrs:
                _TEXT_STATS["guard"] = guard_addrs
                _TEXT_STATS["x"] = x_addrs
            _TEXT_STATS["scan_at"] = time.time()
            if emit:
                emit("  （文本数值：守护 %d 处，X %d 处）"
                     % (len(guard_addrs), len(x_addrs)))
        except Exception as exc:
            if emit:
                emit("  （文本数值扫描失败：%s）" % exc)


def _apply_text_stats(h):
    """读缓存的文本地址，把游戏内数值同步进统计。"""
    try:
        # 守护数：内存里有**多份文本副本**（实测 5 处=7、2 处=4，7 才是我方的）。
        # 取"出现次数最多"的值（我方卡牌/界面上的副本更多），平票时取较大值。
        from collections import Counter as _C
        vals = []
        for a in _TEXT_STATS["guard"]:
            blob = RW.read(h, a, 0x200)
            if not blob:
                continue
            v = _parse_text_value(blob.decode("utf-16-le", "replace"))
            if v is not None:
                vals.append(v)
        if vals:
            cnt = _C(vals)
            best = max(cnt.items(), key=lambda kv: (kv[1], kv[0]))[0]
            if best > int(_BSTAT.get("guard_dead") or 0):
                _BSTAT["guard_dead"] = best
        for a, ctx in _TEXT_STATS["x"]:
            blob = RW.read(h, a, 0x200)
            if not blob:
                continue
            v = _parse_text_value(blob.decode("utf-16-le", "replace"))
            if v is None:
                continue
            if "梦魇" in ctx:
                _BSTAT["nightmare_in"] = max(int(_BSTAT.get("nightmare_in") or 0), v)
            elif "进化" in ctx:
                _BSTAT["evolved"] = max(int(_BSTAT.get("evolved") or 0), v)
    except Exception:
        pass


def _special_aura_map():
    """「光环特殊卡号」→ 来源卡名（来自卡表的 claim_aura 里的 base_card_id）"""
    if _SPECIAL_AURA_MAP[0] is not None:
        return _SPECIAL_AURA_MAP[0]
    m = {}
    for name, card in _cards_by_name().items():
        for sk in (card.get("skills") or []):
            if sk.get("action") != "claim_aura":
                continue
            mt = re.search(r"base_card_id=(\d+)", str(sk.get("option") or ""))
            if mt:
                m.setdefault(int(mt.group(1)), []).append(name)
    _SPECIAL_AURA_MAP[0] = m
    return m


def _claimed_auras(h, me):
    """
    直接读玩家的 SpecialCardList（被认领的光环）——**不依赖来源牌是否还活着**。
    这样"先拿光环、后开插件"也能显示（用户要求）。
    """
    out = []
    try:
        obj = int(me.get("obj"), 16)
        ids = RW.read_special_cards(h, obj)
        if not ids:
            return out
        m = _special_aura_map()
        byname = _cards_by_name()
        # 用户 2026-10-18：**同一个光环卡号**造成的数值变化写在一条里。
        #   （卡号是共享的：60000040 = 涸绝+猛犸象，60000030 = 虫风花/辛西亚/阿丽雅）
        from collections import Counter as _Counter
        cnt = _Counter(int(x) for x in ids)
        for cid, times in cnt.items():
            names = m.get(cid) or []
            # 共享卡号（60000040 = 涸绝 + 猛犸象）不能直接把候选数值相加，
            # 用"我方这局见过哪些来源牌"来挑：只保留见过的候选（唯一就选中）。
            try:
                _seen = _seen_names.get("player") or {}
                _hit = [n for n in names if _seen.get(n)]
                if len(_hit) == 1:
                    names = _hit
            except Exception:
                pass
            # 用户 2026-10-18：**先乘算再加算** ——
            #   同一张卡的效果发动过几次就 ×几次；同一个卡号的条目最后加在一起。
            if not names:
                continue
            # 共享卡号：用「这局实际见过的来源」挑；多个都见过就按「加算」合并。
            # 只挑**一个**来源（共享卡号不能把所有候选都算上，否则数值会翻倍）。
            #   优先「入场即认领（when_play）」的候选 —— 入场曲光环会在牌在场时发动；
            #   其次挑这局见过的；都没有就用第一个。
            _pick = None
            _defs = {}
            for n in names:
                cd = byname.get(n) or {}
                _timings = [x.get("timing") for x in (cd.get("skills") or [])
                            if x.get("action") == "claim_aura"]
                _defs[n] = _timings
            _seen = {}
            try:
                _seen = _seen_names.get("player") or {}
            except Exception:
                pass
            _play = [n for n in names if "when_play" in (_defs.get(n) or [])]
            _seen_play = [n for n in _play if _seen.get(n)]
            _seen_any = [n for n in names if _seen.get(n)]
            _pick = ((_seen_play or _play or _seen_any or names) or [None])[0]
            items = []
            if _pick:
                cd = byname.get(_pick)
                if cd:
                    items.append(_aura_item("player", _pick, cd, "claim_aura",
                                            int(me.get("grade") or 0)))
            if not items:
                continue
            item = dict(items[0])
            # 先乘算（发动次数）再加算（同卡号各来源）
            item["atk"] = int(item["atk"]) * int(times)
            item["hp"] = int(item["hp"]) * int(times)
            item["source"] = "光环卡号 %d ×%d" % (cid, times)
            item["cond"] = dict(item.get("cond") or {},
                                atk=item["atk"], hp=item["hp"])
            out.append(item)
    except Exception:
        pass
    return out


def _minion_buff_entries():
    """
    我方每只随从**额外获得的身材**（用户选 B：只给总量，不区分来源）。

    算法：当前面板 - 卡面基础（进化过的按进化/超进化面板算基数），
    得到的差值就是它吃到的加成；同一只随从一行。
    """
    out = []
    try:
        byid = _cards_by_id()
        for c in _LAST_MY["cards"]:
            try:
                cid = int(c.get("card_id") or 0)
            except Exception:
                cid = 0
            card = byid.get(cid) or {}
            if not card:
                continue
            base_a = int(card.get("attack") or 0)
            base_h = int(card.get("health") or 0)
            ev = card.get("evolve") or {}
            sp = card.get("superEvolve") or {}
            form = int(c.get("__form") or 0)
            if form >= 2 and sp:
                base_a, base_h = int(sp.get("attack") or base_a), int(sp.get("health") or base_h)
            elif form >= 1 and ev:
                base_a, base_h = int(ev.get("attack") or base_a), int(ev.get("health") or base_h)
            try:
                atk = int(c.get("atk") or 0)
                life = int(c.get("life") or 0)
            except Exception:
                continue
            da, dh = atk - base_a, life - base_h
            if da == 0 and dh == 0:
                continue                      # 没加成就不占位
            out.append({"desc": "%s %d/%d" % (c.get("name") or "?", atk, life),
                        "value": "%+d/%+d" % (da, dh),
                        "atk": da, "hp": dh})
    except Exception:
        pass
    return out


def _feed_write(payload):
    """写 feed 时自动带上：光环 / 其他随从效果 / 本局进化次数"""
    p = dict(payload or {})
    p["leaderEffects"] = leader_effects_for_feed()
    # 用户 2026-10-18：随从逐个增益**不进面板**（只作我核对光环/X 的参考数据）。
    p["specialEffects"] = _special_effects_for_feed()
    try:
        # 参考数据单独落盘，面板不显示
        import json as _json
        with io.open(os.path.join(LOGDIR, "_minion_buffs.json"), "w",
                     encoding="utf-8") as _fh:
            _json.dump(_minion_buff_entries(), _fh, ensure_ascii=False)
    except Exception:
        pass
    p["evolveCount"] = int(_BSTAT["evolved"])
    return FEED.write(p)


def _publish_leader_effects_on_feed():
    """
    把当前光环列表补写进 overlay.json 的 `leaderEffects`。

    为什么单独一步：战斗结果由 SIM.feed_overlay() 写（一个完整 payload），
    里面没有光环字段；这里有，但**不能**整个文件重写 —— 那会把这个时间窗里
    别的东西（阶段切换 / 新一局信号）覆盖掉。

    走 `FEED.update()`：读-改-写整体原子替换，只动 `leaderEffects`。
    旧实现是 `open(fp,"w")` 直接写回，既可能被面板读到半截，
    失败时又只有 `except Exception: pass`（没有任何痕迹）。
    """
    return FEED.update({"leaderEffects": leader_effects_for_feed(),
                        "specialEffects": _special_effects_for_feed(),
                        "evolveCount": int(_BSTAT["evolved"])})


def _leader_store_path():
    return os.path.join(HERE, "logs", "_leader_effects.json")


def _leader_load():
    """
    **不再**从磁盘恢复光环（2026-10-18 用户反馈：会带上上一局的错误状态）。
    现在光环直接从玩家 SpecialCardList 读，不依赖旧文件。
    """
    return _LEADER_EFFECTS


def _leader_load_unused():
    """旧实现（保留备查）：启动时把上次的效果读回来。"""
    global _LEADER_EFFECTS
    try:
        p = _leader_store_path()
        if os.path.isfile(p):
            # 用户 2026-10-18 反馈：上一局的光环会残留到这一局。
            # 超过 10 分钟的文件按"上一局"处理，不加载（重启插件时对局内的才沿用）。
            try:
                if time.time() - os.path.getmtime(p) > 600:
                    return _LEADER_EFFECTS
            except Exception:
                pass
            import json as _json
            with io.open(p, encoding="utf-8") as fh:
                data = _json.load(fh)
            if isinstance(data, list):
                _LEADER_EFFECTS = data
    except Exception:
        pass
    return _LEADER_EFFECTS


def _leader_save():
    """
    把效果写盘（走 overlay_feed 的原子写：PID 临时名 + fsync + os.replace）。

    以前是自己拼一个固定 `.tmp`：如果上次写到一半崩了，残留的 `.tmp`
    下次还会被覆盖（不算错），但多进程/多实例时会互相踩。
    """
    try:
        p = _leader_store_path()
        FEED.write_atomic(p, list(_LEADER_EFFECTS))
    except Exception:
        pass


def _aura_names():
    """光环卡号 → 光环名（从 lib/knowledge/auras.json 读，一次缓存）"""
    global _AURA_NAMES
    try:
        return _AURA_NAMES
    except NameError:
        pass
    import json as _json
    _AURA_NAMES = {}
    try:
        p = os.path.join(os.path.dirname(HERE), "dsh-shadowverse-huanjing",
                         "lib", "knowledge", "auras.json")
        with io.open(p, encoding="utf-8") as fh:
            _AURA_NAMES = _json.load(fh)
    except Exception:
        _AURA_NAMES = {}
    return _AURA_NAMES


def _aura_of_card(card):
    """
    取一张牌「会赋予哪个光环」：返回 (光环名, atk, hp)。

    优先用光环自己的名字（僵尸光环 / 精灵光环 / 谭涛光环）；
    数值仍然从来源卡文本里的 +N/+M 解析（不同牌给的光环数值可能不同）。
    """
    aid = None
    for s0 in (card.get("skills") or []):
        if s0.get("action") != "claim_aura":
            continue
        opt = _parse_opt(s0.get("option"))
        aid = opt.get("base_card_id") or opt.get("baseCardId")
        break
    names = _aura_names()
    aname = names.get(str(aid)) if aid is not None else None
    _, atk, hp = _aura_desc(card)
    if not aname:
        # 查不到光环名时退回"效果描述"，但不要用来源卡名
        aname, atk2, hp2 = _aura_desc(card)
        atk = atk or atk2
        hp = hp or hp2
    return aname, atk, hp


def _aura_timing(card):
    """这张牌的光环在**什么时点**生效（when_play / when_destroy / when_destroy_other）"""
    for s0 in (card.get("skills") or []):
        if s0.get("action") == "claim_aura":
            return str(s0.get("timing") or "")
    return ""


def _aura_condition(desc):
    """
    从光环效果文本里解析出结构化条件。

    例：
        "精灵·随从进场 +1/+0"                 → {tribe:"精灵", maxStar:None, atk:1, hp:0}
        "幻境等级3或以下的随从进场 +1/+1"      → {tribe:None, maxStar:3, atk:1, hp:1}
    """
    import re as _re
    d = str(desc or "")
    tribe = None
    for t in ("中立", "精灵", "皇家护卫", "梦魇", "龙族"):
        if t in d:
            tribe = t
            break
    max_star = None
    m = _re.search(r"等级(\d+)或以下", d)
    if m:
        max_star = int(m.group(1))
    # 「『腐臭的僵尸』进场」这类是**按具体卡名**加成的光环。
    # 之前没解析出来 → 传给模拟时条件为空 → 变成"所有随从 +N/+N"，严重超标。
    name = None
    mq = _re.search(r"『([^』]+)』", d)
    if mq:
        name = mq.group(1).strip()
    m2 = _re.search(r"\+(\d+)/\+(\d+)", d)
    atk = int(m2.group(1)) if m2 else 0
    hp = int(m2.group(2)) if m2 else 0
    return {"tribe": tribe, "maxStar": max_star, "name": name,
            "atk": atk, "hp": hp}


def _aura_threshold(card):
    """从「其他随从被破坏 N 个后发动」里解析出 N（默认 4）"""
    import re as _re
    for s0 in (card.get("skills") or []):
        if s0.get("action") != "claim_aura":
            continue
        c = str(s0.get("condition") or "")
        m = _re.search(r"%\s*(\d+)\s*=\s*0", c)
        if m:
            try:
                return max(1, int(m.group(1)))
            except Exception:
                pass
    return 4


# ─────────────────────────────────────────────────────────────────────────────
# 光环解析失败记账（HANDOFF §7.1 / §8-8：不能再静默失效）
#
# 为什么会静默失效：`_aura_desc` / `_aura_condition` 是靠**解析卡面文本**得到
# 「种族 / 星级上限 / 卡名」的。官方一改说法（"森之随从"、"阶级 3 以下"、"自己的
# 其它随从"…），正则就不匹配，条件变成空 dict；模拟侧把"空条件"理解成
# **所有随从 +N/+M** —— 胜率会系统性偏高，而且界面上看不出任何异常。
# 所以：一旦解析结果可疑，就往 logs/光环解析失败.log 追加一行（带卡名、时点、原因、
# 卡面原文），同一个 (卡名, 原因) 只记一次，避免每场刷屏。
# ─────────────────────────────────────────────────────────────────────────────
_AURA_WARNED = set()


def _aura_log_path():
    return os.path.join(LOGDIR, "光环解析失败.log")


def _aura_warn(card_name, timing, reason, detail):
    """
    记一条光环解析告警；同一个 (卡名, 时点, 原因) 只写一次。

    返回 True 表示这次真的写了一行（测试用）。
    """
    key = (card_name or "?", str(timing or ""), reason)
    if key in _AURA_WARNED:
        return False
    _AURA_WARNED.add(key)
    line = "[%s] %s | timing=%s | %s | %s\n" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), card_name or "?", timing or "-",
        reason, detail)
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        with io.open(_aura_log_path(), "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass                      # 写日志失败不能影响判定
    return True


def _aura_parse_check(card, timing, desc, atk, hp):
    """
    光环解析结果自检：可疑就写日志，返回 True 表示解析看起来正常。

    三类可疑：
      1. 数值没解析出来（卡面里没有 `+N/+M`）→ 光环加成会当成 +0/+0；
      2. 「自己的…进入战场」没匹配上 → desc 退回「随从进场」= 所有随从；
      3. 匹配上了但 desc 里没有种族/星级/卡名 → 条件为空 = 所有随从。
    第 2、3 类都会让模拟**高估我方**（光环加给了不该加的随从）。
    """
    name = card.get("name") or "?"
    txt = _card_text(card)
    problems = []

    if not atk and not hp:
        problems.append(("数值未解析", "卡面文本里找不到 +N/+M"))

    m = re.search(r"自己的(.{0,14}?)进入战场", txt)
    if not m:
        problems.append(("作用对象未解析",
                         "「自己的…进入战场」没匹配上，回退成「所有随从」"))
    else:
        cond = _aura_condition(desc)
        if not (cond.get("tribe") or cond.get("maxStar") or cond.get("name")):
            problems.append(("条件为空",
                             "desc=%r 里解析不出种族/星级/卡名，会被当成「所有随从」"
                             % (desc,)))

    for reason, detail in problems:
        _aura_warn(name, timing, reason,
                   "%s（desc=%r，卡面=%r）"
                   % (detail, desc, txt[:80]))
    return not problems


def _aura_item(side, name, card, timing, rnd):
    """
    生成一条光环条目（含 cond），顺手做一次解析自检。

    检测循环里有 3 个地方要产出条目（入场即生效 / 自身谢幕 / 其他随从死亡计数），
    统一走这里，避免三份重复代码各自漏掉自检。
    """
    aname, atk, hp = _aura_of_card(card)
    effect_text = _aura_desc(card)[0]
    _aura_parse_check(card, timing, effect_text, atk, hp)
    return {
        "side": side, "source": name,
        "desc": effect_text, "auraName": aname,
        "round": rnd,
        "cond": dict(_aura_condition(effect_text), atk=atk, hp=hp),
        "atk": atk, "hp": hp,
    }


def detect_leader_effects(battle):
    """
    找出「会给主战者挂光环」的牌（双方都算，面板只显示我方）。

    三条规则（都是实测纠正出来的）：

    1. **每张来源牌返回一条**，不做合并（合并交给 merge_leader_effects）。
    2. **谢幕曲光环（when_destroy）要等这张牌自己死了才生效**。
       辛西亚 / 涸绝 属于这类；牌活着时根本还没挂上光环。
    3. **when_destroy_other（阿丽雅）不同**：
       它是「其他随从被破坏 N 个后发动」，**而且这张牌必须还在场上**。
       所以：牌活着时累积"其他随从死亡数"，满 N 就发动一次（可重复），
       牌死了（离开战场）就作废。
    """
    if not battle:
        return []
    global _seen_names
    byname = _cards_by_name()
    out = []
    _now_seen = {}

    for _side_key in ("player", "opponent"):
        side_battle = battle.get(_side_key) or {}
        rnd = int(side_battle.get("level") or 0)
        board = side_battle.get("board") or []
        pending = _PENDING_AURA.setdefault(_side_key, {})

        # ① 先把本侧**全部**随从数清楚（同名多张分别计数），再算死亡数。
        #    ⚠️ 顺序很关键：旧实现是"边遍历边算"，算到阿丽雅时后面那些
        #    还没遍历到的随从就被当成"已经不在了" → 每场都虚报死亡数。
        cur_names = Counter()
        for f in board:
            _n = f.get("name")
            if _n:
                cur_names[_n] += 1
        prev_names = _seen_names.get(_side_key) or Counter()
        deaths = sum(max(0, prev_names[_n] - cur_names[_n]) for _n in prev_names)
        _now_seen[_side_key] = cur_names

        for f in board:
            name = f.get("name")
            card = byname.get(name)
            if not card:
                continue
            aura_skills = [s0 for s0 in (card.get("skills") or [])
                           if s0.get("action") == "claim_aura"]
            if not aura_skills:
                continue
            tm = _aura_timing(card)

            if tm.startswith("when_destroy_other"):
                # 阿丽雅：在场上 + 其他随从死亡数达标 → 发动
                st = pending.get(name)
                if not isinstance(st, dict):
                    st = {"round": rnd, "deaths": 0, "need": _aura_threshold(card)}
                    pending[name] = st
                # 本侧这一轮死了几个（**只数本侧**；阿丽雅自己还活着，差额为 0）
                st["deaths"] += deaths
                while st["deaths"] >= st["need"]:
                    st["deaths"] -= st["need"]
                    out.append(_aura_item(_side_key, name, card, tm, st["round"]))
                continue

            if tm.startswith("when_destroy"):
                pending.setdefault(name, rnd)      # 还活着 → 只记账
                continue

            # 入场曲类（when_play）→ 登场即生效
            out.append(_aura_item(_side_key, name, card, tm, rnd))


        # 记账里的牌如果**自己离开战场**：
        #   · 数值型（自谢幕曲）→ 说明它死了，光环生效
        #   · 字典型（阿丽雅 when_destroy_other）→ 牌没了，作废
        for name in list(pending.keys()):
            if name in cur_names:
                continue
            st = pending.pop(name)
            if isinstance(st, dict):
                continue                      # 阿丽雅离场：不发动
            card = byname.get(name)
            if not card:
                continue
            out.append(_aura_item(_side_key, name, card, _aura_timing(card), st))

    _seen_names = _now_seen
    return out


def _recompute_leader_text():
    """
    按 desc 分组，把每组的数值加起来，生成展示文案。

    份数 = 组内**不同来源牌**的数量。
    """
    groups = {}
    for e in _LEADER_EFFECTS:
        d = e.get("desc") or e.get("name") or "?"
        g = groups.setdefault(d, {"count": 0, "atk": 0, "hp": 0, "sources": []})
        g["count"] += 1
        g["atk"] += int(e.get("atk") or 0)
        g["hp"] += int(e.get("hp") or 0)
        src = e.get("source")
        if src and src not in g["sources"]:
            g["sources"].append(src)
    for e in _LEADER_EFFECTS:
        d = e.get("desc") or e.get("name") or "?"
        g = groups.get(d) or {"count": 1, "atk": 0, "hp": 0, "sources": []}
        # 文案就是「效果 + 叠加后的数值」，不带名字、不带份数
        # （用户要求：不要给光环取名字，直接显示效果叠加了多少）
        e["text"] = d + " +" + str(g["atk"]) + "/+" + str(g["hp"])
        e["groupCount"] = g["count"]
        e["groupAtk"] = g["atk"]
        e["groupHp"] = g["hp"]


def merge_leader_effects(new_items):
    """
    把新发现的光环并进累积列表。

    去重键 = (效果, 来源, 数值)。**数值不同要分别保留、面板按效果求和**：
      · 辛西亚基础形态谢幕 +1/+0、进化形态谢幕 +2/+0；
      · 进化后的谢幕曲会拉一个"进化前的自己"，两个都死了应该合计 **+3/+0**；
      · 只按 (效果, 来源) 去重会把 +1 的那条吞掉，永远只显示最大值。
    同一张牌被重复检测到（值相同）仍然只算一次，不会虚增。
    """
    global _LEADER_EFFECTS
    # 「已认领光环」是按次数实时重算的 → 每次用新的整批替换旧的，
    # 否则次数变了会不断追加出重复条目。
    _claimed = [e for e in (new_items or [])
                if str(e.get("source") or "").startswith("光环卡号")]
    if _claimed:
        _LEADER_EFFECTS = [e for e in _LEADER_EFFECTS
                           if not str(e.get("source") or "").startswith("光环卡号")]
        new_items = [e for e in (new_items or [])
                     if not str(e.get("source") or "").startswith("光环卡号")]
    for e in _claimed + list(new_items or []):
        key = (e.get("desc") or e.get("name"), e.get("source"),
               int(e.get("atk") or 0), int(e.get("hp") or 0))
        if not any((x.get("desc") or x.get("name"), x.get("source"),
                    int(x.get("atk") or 0), int(x.get("hp") or 0)) == key
                   for x in _LEADER_EFFECTS):
            _LEADER_EFFECTS.append(dict(e))
    _recompute_leader_text()
    _leader_save()
    return _LEADER_EFFECTS


def clear_leader_effects():
    """清空（新一局开始 / 关闭插件）"""
    global _LEADER_EFFECTS
    _LEADER_EFFECTS = []
    try:
        p = _leader_store_path()
        if os.path.isfile(p):
            os.remove(p)
    except Exception:
        pass
    return _LEADER_EFFECTS


def _save_calibration_shot(battle, res, emit):
    """
    预测结果很极端（0% 或 100%）时，把当时的画面和阵容存下来做对照。

    为什么要做：用户反馈过「判 100% 输，实际却赢了」。
    有了截图 + 双方阵容，就能直接核对是**读错了**还是**规则算错了**。
    """
    try:
        import os as _os, time as _t
        wr = res.get("winRate") or {}
        p_win = wr.get("player")
        if p_win is None:
            return
        # 每一场都存（不只极端值）：用户反馈过判必输却赢了，
        # 没有画面证据就没法判断是读错还是算错。清理程序会在 2 小时后删掉。
        if False:
            return
        stamp = _t.strftime("%m%d-%H%M%S")
        base = _os.path.join(HERE, "logs")
        png = _os.path.join(base, "预测对照-%s.png" % stamp)
        txt = _os.path.join(base, "预测对照-%s.txt" % stamp)
        try:
            img = GRAB.grab_screen()
            if img is not None:
                img.save(png)
        except Exception:
            pass
        lines = ["预测：胜 %.1f%%  败 %.1f%%" % (p_win, wr.get("opponent") or 0), ""]
        for side, label in (("player", "我方"), ("opponent", "对方")):
            d = battle.get(side) or {}
            lines.append("%s  等级%s  血量%s  随从%d" % (
                label, d.get("level"), d.get("raw_hp"), len(d.get("board") or [])))
            for f in (d.get("board") or []):
                lines.append("    %s★ %s/%s %s" % (
                    f.get("star"), f.get("attack"), f.get("health"), f.get("name")))
            lines.append("")
        with io.open(txt, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        emit("  （极端预测已存对照：%s）" % _os.path.basename(txt))
    except Exception as e:
        try:
            emit("  （对照快照失败：%s）" % e)
        except Exception:
            pass


def board_of(p):
    """
    把内存读到的卡列表转成引擎输入。

    ⚠️ 要过滤掉**已阵亡**的随从：阵亡时读到的是 `0★ 0/0`，
    如果当成还活着会严重扭曲战斗模型（多算出伤害）。
    """
    out = []
    for c in p.get("cards") or []:
        star = int(c.get("star") or 0)
        atk = int(c.get("atk") or 0)
        life = int(c.get("life") or 0)
        if star <= 0 or life <= 0:      # 阵亡/占位对象
            continue
        out.append({"name": c.get("name") or f"card{c.get('card_id')}",
                    "attack": max(0, atk),
                    "health": max(1, life),
                    "star": max(1, star)})
    return out


def _sleep_sec(args):
    """
    主循环的睡眠时长：**战斗阶段要快**。

    战斗中 0.08 秒一轮（2026-10-19 从 0.15 加密）——
    召唤出来的随从常常活不过 1 秒，战斗到后面还会加速；
    梦魇「进场即死」在 150ms 采样下会漏计，80ms 能把漏计压下去。
    只读缓存一次 1~3 ms，12.5 Hz 对 CPU 几乎无感。
    其它阶段用 --interval（默认 0.8 秒），省 CPU。
    """
    try:
        return 0.08 if _CUR_PHASE[0] == "battle" else float(args.interval)
    except Exception:
        return 0.8


def read_once(h, cards_db, cache_only=False):
    """
    读一次双方状态；返回 (battle, 原始快照) 或 (None, 原因)。

    cache_only=True → **绝不触发冷扫描**（开战瞬间用）。
    一次冷扫描要 20~40 秒，会把整场战斗错过；没有缓存时宁可快速失败，
    让上层报"读取中"，等后台线程把缓存热好。
    """
    snap = RW.snapshot(h, cards_db, force_cold=(False if cache_only else None))
    if "error" in snap:
        return None, snap["error"]
    me = next((p for p in snap.get("players") or [] if p.get("side") == "我方"), None)
    op = next((p for p in snap.get("players") or [] if p.get("side") == "对方"), None)
    if not me or not op:
        return None, "只读到一方玩家"
    battle = {
        "phase": "battle",
        "player": {"cards_unknown": bool(me.get("cards_unknown")),
                   # raw_hp 保留原始血量：被淘汰时是 0 或负数，clamp 后就看不到了
                   "raw_hp": int(me.get("life") or 0),
                   "hp": max(1, int(me.get("life") or 1)),
                   "level": max(1, int(me.get("grade") or 1)),
                   "hero": int(me.get("hero_id") or 0),
                   "board": board_of(me)},
        "opponent": {"cards_unknown": bool(op.get("cards_unknown")),
                     "hp": max(1, int(op.get("life") or 1)),
                     "level": max(1, int(op.get("grade") or 1)),
                     "hero": int(op.get("hero_id") or 0),
                     "board": board_of(op)},
    }
    _battle_stats_update(snap, h)
    return battle, snap


# ── 判定参数热读（delay / sims）──────────────────────────────
JUDGE_SET = os.path.join(LOGDIR, "judge-settings.json")


def read_hot_settings(default_delay, default_sims):
    """每轮循环读一次 judge-settings.json（内容比较），改了立即生效，不重启进程"""
    try:
        with open(JUDGE_SET, encoding="utf-8") as fh:
            d = json.load(fh) or {}
    except Exception:
        return default_delay, default_sims
    delay, sims = default_delay, default_sims
    try:
        delay = max(0.0, min(3.0, float(d.get("delay", delay))))
    except (TypeError, ValueError):
        pass
    try:
        sims = max(500, min(200000, int(d.get("sims", d.get("budget", sims)))))
    except (TypeError, ValueError):
        pass
    return delay, sims


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=0.8)
    ap.add_argument("--sims", type=int, default=1200)
    ap.add_argument("--timeout", type=int, default=10800)
    ap.add_argument("--delay", type=float, default=0.0,
                    help="开战后首次读取的等待秒数（战斗开始时效果此前后已结算）")
    args = ap.parse_args()

    os.makedirs(LOGDIR, exist_ok=True)
    log_path = os.path.join(LOGDIR, time.strftime("内存判定-%Y%m%d-%H%M%S.txt"))
    logf = open(log_path, "w", encoding="utf-8-sig")

    def emit(s=""):
        print(s, flush=True)
        logf.write(s + "\n")
        logf.flush()

    pid, waited = None, 0
    while not pid and waited < args.timeout:
        pid, _p = find_game_pid()
        if not pid:
            time.sleep(3)
            waited += 3
    if not pid:
        emit("等待超时：游戏没启动")
        return 1

    h = open_proc(pid)
    RW.WD_PID[0] = pid
    cards_db = load_cards()
    emit("=== 开战判定（纯内存：等级/血量/随从全部来自内存）===")
    emit(f"进程 {pid}  卡片 {len(cards_db)} 张")
    emit(f"日志 {log_path}")
    emit("")

    t_start = time.time()
    # 后台线程持续刷新世界缓存（主循环永不因扫描阻塞）
    try:
        _warmer_stop, _warmer_th = _start_cache_warmer(h, cards_db, emit)
    except Exception as _e:
        _warmer_stop, _warmer_th = None, None
        emit(f"  （后台刷新线程启动失败：{_e}）")

    tracker = PhaseTracker()      # 阶段状态机：去抖 + 只在确认切换时给沿
    done = 0
    last_at = 0.0
    last_feed = [0.0]
    has_result = [False]
    _last_result = [None]  # 最近一次战果
    _result_at = [0.0]     # 战果产生时间
    _battle_dur = [0.0]    # 本场战斗持续多久（秒）
    _battle_started_at = 0.0
    _warmed = [False]     # 世界缓存是否已预热过（只扫一次）

    threading.Thread(target=_scan_text_stats, args=(emit,), daemon=True).start()
    _leader_load()          # 重启后把上次的光环读回来
    _EMIT[0] = emit
    _evolve_load()          # 重启后把本局进化数读回来（用户要求保留）
    _counters_load()        # 守护/梦魇计数同样保留

    # 用户要求：**不管对局什么时候开插件，都要尽快看到数据**。
    # 先在主线程立刻试一次（缓存热时 1 秒内出结果；冷的话后台线程正在扫）。
    try:
        _s0 = RW.snapshot(h, cards_db, force_cold=False)
        if not _s0.get("error"):
            # 启动时等级 <= 2 → 大概率是刚开的新一局：清掉上一局残留的进化数/光环
            try:
                _me0 = next((p for p in _s0.get("players") or []
                             if p.get("side") == "我方"), None)
                if _me0 is not None and int(_me0.get("grade") or 9) <= 2:
                    _BSTAT.update(nightmare_in=0, guard_dead=0,
                                  evolved=0, evolved_ids=set())
                    clear_leader_effects()
                    emit("  （启动检测到新一局：已清空上一局的光环/计数/进化数）")
            except Exception:
                pass
            _battle_stats_update(_s0, h)
            # 启动时如果已经出局（life<=0），把上一局结算掉 ——
            # 之前判定器重启后不会补记，导致战绩里那局 hp/rank 都是空（用户实测）。
            try:
                _me0 = next((p for p in _s0.get("players") or []
                             if p.get("side") == "我方"), None)
                if _me0 is not None and int(_me0.get("life") or 0) <= 0:
                    _maybe_record_game_over(_me0.get("hero_id"),
                                            int(_me0.get("life") or 0),
                                            None, emit)
            except Exception:
                pass
            _b0 = SIM.build_battle(_s0.get("players") or [])
            if _b0:
                merge_leader_effects(detect_leader_effects(_b0))
            _ph0 = "battle" if _b0 and any(
                (s.get("board") for s in (_b0.get("player"), _b0.get("opponent")))) else "operate"
            _feed_write({"phase": _ph0, "win": None,
                         "leaderEffects": leader_effects_for_feed(),
                         "updatedAt": time.strftime("%H:%M:%S")})
            emit("  （启动即读到局面：直接推送光环/进化）")
    except Exception as _e0:
        emit("  （启动首读失败，等后台缓存：%s）" % str(_e0)[:60])
    _last_gameover_check = [0.0]
    # 这次"离开对局"是从什么时候开始的：内存与屏幕同时判"不在对局"满 45 s 才清光环，
    # 避免过场/加载瞬间把面板内容清掉
    _out_of_match = [0.0]
    # 战斗阶段的**高频光环抓取**：每 0.08 秒读一次阵容（2026-10-19 从 0.15 加密）。
    # 原先是 5 秒一次，但召唤出来的随从往往活不过 5 秒
    #（而且战斗到后面还会加速），根本抓不到 → 用户反馈"光环不显示"。
    _last_aura_poll = [0.0]
    _AURA_POLL_SEC = 0.08
    _feed_sig = [None]           # 效果/进化数签名（变化就立刻推送）

    while time.time() - t_start < args.timeout:
        # 热读判定参数：改了 judge-settings.json 立即生效，PID 不变
        args.delay, args.sims = read_hot_settings(args.delay, args.sims)
        img = GRAB.grab_screen()
        if img is None:
            time.sleep(_sleep_sec(args))
            continue
        ph = PHASE.phase_of(img)
        now = time.time()
        # ⚠️ 必须用状态机：裸电平判断会被过渡态骗，
        #    一次开战算两次、或者整场漏掉。
        edge, cur_phase = tracker.update(ph, now)
        try:
            _BATTLE_WINDOW[0] = (cur_phase == "battle") or (edge == "rising")
        except Exception:
            pass
        _CUR_PHASE[0] = cur_phase
        rising = (edge == "rising")
        changed = (edge is not None)

        # ── 战斗阶段：高频抓光环 ──
        # 只读缓存（一次 1~3 ms），所以 0.08 秒一轮也几乎不占 CPU。
        # 这样"僵尸被召唤 → 死亡 → 上光环"这条链在几秒内就能被看到。
        if (cur_phase in ("battle", "operate")
                and now - _last_aura_poll[0] >= _AURA_POLL_SEC):
            _last_aura_poll[0] = now
            try:
                _s2 = RW.snapshot(h, cards_db, force_cold=False)
                if not _s2.get("error"):
                    # ⚠️ 进化/召唤的统计必须挂在高频轮询上：
                    #    只靠"开战瞬间 + 每 5 秒"会漏掉战斗中进化后很快死掉的随从
                    #    （用户实测：慈颜的拥趸召唤的新绿的妖精在战斗阶段进化，没算进次数）。
                    _battle_stats_update(_s2, h)
                    _b2 = SIM.build_battle(_s2.get("players") or [])
                    _new2 = detect_leader_effects(_b2 or {})
                    if _new2:
                        _n0 = len(_LEADER_EFFECTS)
                        merge_leader_effects(_new2)
                        if len(_LEADER_EFFECTS) > _n0:
                            emit("  ☆ 抓到光环：%s" % ", ".join(
                                str(e.get("desc")) for e in _new2[:3]))
                    # 用户要求：面板要**实时**（动作完成后 1 秒内）
                    # 效果/进化数一变就立刻写 feed，不等 1 秒周期。
                    _sig = (len(_LEADER_EFFECTS),
                            tuple((e.get("desc"), e.get("atk"), e.get("hp"))
                                  for e in leader_effects_for_feed()),
                            tuple((sp.get("desc"), sp.get("value"))
                                  for sp in _special_effects_for_feed()),
                            int(_BSTAT["evolved"]))
                    if _sig != _feed_sig[0]:
                        _feed_sig[0] = _sig
                        last_feed[0] = 0.0      # 下一轮立即写 feed
            except Exception:
                pass

        if changed or (now - last_feed[0] > 1.0):
            last_feed[0] = now
            try:
                if cur_phase == "battle":
                    if not has_result[0]:
                        # 开战了但还没算出来 → 显示"读取中"
                        _feed_write({"phase": "battle", "win": None})
                elif (cur_phase == "operate"
                      or (cur_phase and "operate" in str(cur_phase))):
                    # 运营阶段：**不显示胜率**
                    has_result[0] = False
                    _feed_write({"phase": "operate", "win": None,
                                "leaderEffects": leader_effects_for_feed(),
                                "updatedAt": time.strftime("%H:%M:%S")})
                # 用户 2026-10-18：**运营阶段不显示胜率**（战果只在战斗阶段展示）
                # 原来战斗结束后会保留 4 秒战果，现在一律直接切回"运营ing"。
                else:
                    has_result[0] = False
                    _feed_write({"phase": ph, "win": None,
                                "leaderEffects": leader_effects_for_feed()})
            except Exception:
                pass

        if not rising:
            # 定期检查「整局是否已结束」：被淘汰后不会再开战，
            # 只在开战时检查会永远等不到。每 5 秒轻量读一次（缓存命中约 1ms）。
            # 不再限制"非战斗阶段"：屏幕判据可能把主界面误判成 battle，
            # 那样这里就永远不执行 → 悬浮层卡在"读取中"。每 5 秒都查一次。
            if (now - _last_gameover_check[0]) > 1.5:
                _last_gameover_check[0] = now
                try:
                    _snap = RW.snapshot(h, cards_db)
                    _battle_stats_update(_snap, h)
                    # ── 用内存判断"是否在影之幻境对局内" ──
                    # 不在对局时世界数据里没有双方玩家引用，snapshot 会报
                    # 「WorldData 里没找到两个玩家引用（可能不在对局）」。
                    # 屏幕上靠徽章颜色判断会被主界面的红宝石骗到（实测），
                    # 内存才是可信信号。
                    _err = str(_snap.get("error") or "")
                    _players = _snap.get("players") or []
                    _me_now = next((p for p in _players if p.get("side") == "我方"), None)

                    # ⚠️ 缓存可能指向**上一局**的世界对象：那一局我们已经淘汰（血<=0），
                    #    但对方还活着 → cache_ok 认为"有效"，整局都读到旧数据
                    #    （用户反馈：正在进行的游戏读不到、战绩一直不推进）。
                    #    这里用「屏幕说在局内 + 内存说我们死了」作为失效信号，强制重扫。
                    if (_me_now is not None
                            and int(_me_now.get("life") or 0) <= 0
                            and cur_phase in ("battle", "operate")):
                        if RW.invalidate(pid):
                            emit("  （缓存指向上一局，已失效重扫）")
                            _snap = RW.snapshot(h, cards_db)
                            _battle_stats_update(_snap, h)
                            _err = str(_snap.get("error") or "")
                            _players = _snap.get("players") or []
                            _me_now = next((p for p in _players
                                            if p.get("side") == "我方"), None)

                    if _err or _me_now is None:
                        # 读不到（可能是缓存重建中）→ 不改写阶段，交给屏幕判定。
                        # 只有屏幕也认为不在对局时，才由心跳写成 other。
                        if cur_phase not in ("battle", "operate"):
                            # 长时间同时满足「内存不在对局」+「屏幕不在对局」
                            # → 认定这一局已经离开（退出/重开），清空光环，
                            #   否则面板会在主界面一直显示上一局的光环。
                            if _out_of_match[0] == 0.0:
                                _out_of_match[0] = now
                            elif now - _out_of_match[0] > 45.0:
                                if _LEADER_EFFECTS:
                                    clear_leader_effects()
                                    emit("  （已离开对局，光环面板清空）")
                            try:
                                _feed_write({"phase": "other", "win": None,
                                            "leaderEffects": leader_effects_for_feed(),
                                            "updatedAt": time.strftime("%H:%M:%S")})
                            except Exception:
                                pass
                        time.sleep(_sleep_sec(args))
                        continue
                    _out_of_match[0] = 0.0
                    if not _snap.get("error"):
                        # ── 核对上一场：实际掉血是否落在预测分布里 ──
                        try:
                            _c = _CALIB[0]
                            _me_c = next((p for p in (_snap.get("players") or [])
                                          if p.get("side") == "我方"), None)
                            if _c and _me_c is not None and cur_phase != "battle":
                                _now_hp = int(_me_c.get("life") or 0)
                                _dmg = int(_c["hp_before"]) - _now_hp
                                if _dmg >= 0:
                                    _dist = _c.get("dist") or []
                                    _tot = sum(p for _d, p in _dist) or 1.0
                                    _hit = next((p for d, p in _dist if d == _dmg), 0.0)
                                    _lo = min((d for d, _p in _dist), default=0)
                                    _hi = max((d for d, _p in _dist), default=0)
                                    # 预测"必胜"时分布里没有 toPlayer 条目，
                                    # 实际掉 0 血就是**完全符合**，不能报超范围。
                                    if not _dist and _dmg == 0:
                                        _inside = True
                                        _extra = ""
                                    else:
                                        _inside = bool(_dist) and _lo <= _dmg <= _hi
                                        # 落在 min~max 之间但样本里一次都没出现：
                                        # 大概率是"不可能的伤害值"（分布有间隔），
                                        # 别再报"落在范围内"骗自己。
                                        _extra = ("" if _hit > 0 or not _inside
                                                  else "（该取值 0 样本，概率≈0）")
                                    _txt = ("  【核对】%s 那场：预测掉血 %s~%s，"
                                            "实际掉 %d（该值概率 %.1f%%）→ %s%s" % (
                                                _c["t"], _lo, _hi, _dmg,
                                                100.0 * _hit / _tot,
                                                "落在范围内" if _inside else "❌ 超出范围",
                                                _extra))
                                    emit(_txt)
                                    if not _inside:
                                        # 不符合 → 立刻把这一场的对照材料指出来，
                                        # 并把双方阵容打在日志里，方便直接定位。
                                        try:
                                            import glob as _g
                                            _files = sorted(_g.glob(os.path.join(
                                                HERE, "logs", "预测对照-*.txt")))
                                            if _files:
                                                emit("  ↳ 对照文件：%s" % os.path.basename(_files[-1]))
                                                with io.open(_files[-1], encoding="utf-8") as _fh:
                                                    for _ln in _fh.read().splitlines()[:20]:
                                                        emit("     " + _ln)
                                        except Exception:
                                            pass
                                    _CALIB[0] = None
                        except Exception:
                            pass
                        # 文本数值扫描：对局内还没扫到（启动时在结算/菜单扫不到）
                        # 就每 20 秒重扫一次，直到拿到地址。
                        try:
                            if (not _TEXT_STATS["guard"] and not _TEXT_STATS["x"]
                                    and cur_phase in ("battle", "operate")
                                    and time.time() - float(_TEXT_STATS.get("scan_at") or 0) > 20):
                                threading.Thread(target=_scan_text_stats,
                                                 args=(emit,), daemon=True).start()
                        except Exception:
                            pass
                        # 对局内生的时候记下最近一次有效名次（出局时兜底用）
                        try:
                            if _me and int(_me.get("life") or 0) > 0:
                                _now2 = time.time()
                                if _now2 - float(_GAME_STATE.get("rank_at") or 0) > 8:
                                    _GAME_STATE["rank_at"] = _now2
                                    import read_rank as _RR
                                    _r2 = _RR.snapshot(h, cards_db) or {}
                                    _m2 = _r2.get("mine") or {}
                                    if _m2.get("rank"):
                                        _GAME_STATE["last_rank"] = int(_m2["rank"])
                        except Exception:
                            pass
                        try:
                            _backfill_rank(emit)
                        except Exception:
                            pass
                        try:
                            _sync_rank(emit)
                        except Exception:
                            pass
                        # ⚠️ 每 5 秒也抓一次光环：
                        #    僵尸往往是被谢幕曲在**战斗中**召唤出来的，
                        #    只在"开战那一刻"读阵容会漏掉它 →
                        #    僵尸死了给的光环面板一直不显示。
                        try:
                            _b = SIM.build_battle(_snap.get("players") or [])
                            _new = detect_leader_effects(_b or {})
                            if _new:
                                _before = len(_LEADER_EFFECTS)
                                merge_leader_effects(_new)
                                if len(_LEADER_EFFECTS) > _before:
                                    emit("  ☆ 定时检查发现新光环：%s" % ", ".join(
                                        e.get("text", "") for e in _new[:3]))
                        except Exception:
                            pass
                        _me = next((p for p in (_snap.get("players") or [])
                                    if p.get("side") == "我方"), None)
                        if _me is not None:
                            # 先看是不是新一局（血量恢复 / 换主战者）→ 清空光环
                            _started = _maybe_new_game(_me.get("hero_id"),
                                                       _me.get("life"), emit)
                            # 只在「确实开了新一局」时建记录。
                            #
                            # ⚠️ 不能只看内存里的 over 标志：判定器一重启就丢，
                            #    之后新一局永远检测不到（实测：重启几次后战绩停在旧的一局）。
                            #    所以再补一条**磁盘**判据：上一条记录已经结算（hp 有值），
                            #    而现在读到血量 > 0，就说明是新的一局。
                            try:
                                _games = HIST._load().get("games") or []
                                _last = _games[-1] if _games else None
                                _last_done = bool(_last) and (_last.get("hp") is not None)
                                _alive_now = int(_me.get("life") or 0) > 0
                                # 只有**人还活着**才建记录：启动瞬间读到的是上一局的
                                # 死亡局面（血量 -10），那时不该凭空多出一条。
                                if _alive_now and (not _games or _started or _last_done):
                                    HIST.ensure_game(_me.get("hero_id"))
                            except Exception:
                                pass
                        if _me is not None and int(_me.get("life") or 0) <= 0:
                            _hp = int(_me.get("life") or 0)
                            _rank = None
                            try:
                                import read_rank as RR
                                _r = RR.snapshot(h, cards_db)
                                _mine = (_r or {}).get("mine")
                                if _mine:
                                    _rank = _mine.get("rank")
                            except Exception:
                                _rank = None
                            # 幂等：每 5 秒会检查一次，不能每次都写
                            _maybe_record_game_over(_me.get("hero_id"), _hp,
                                                    _me.get("cards"), emit)
                            try:
                                _feed_write({"phase": "other", "win": None,
                                            "gameOver": False,
                                            "leaderEffects": leader_effects_for_feed(),
                                            "updatedAt": time.strftime("%H:%M:%S")})
                            except Exception:
                                pass
                            emit(f"  ★ 整局结束：名次={_rank or '?'}  最终血量={_hp}")
                except Exception:
                    pass
            time.sleep(_sleep_sec(args))
            continue
        if now - last_at < 20:
            time.sleep(_sleep_sec(args))
            continue
        last_at = now

        _last_result[0] = None      # 新一场开始，清掉上一场战果
        _battle_started_at = time.time()
        emit("=" * 54)
        emit(f"[{time.strftime('%H:%M:%S')}] 检测到开战，立即读取（不等结算）")

        battle = None
        best_battle = [None]   # 补读时保留"更完整"的那一份
        best_total = [0]
        rerolled = [0]
        # 开战瞬间：先等后台线程把缓存热好（最多 3 秒），再只用缓存读。
        # 这样"开战 → 出结果"通常 < 1 秒，而不是被 20~40 秒的冷扫描拖住。
        _warm_deadline = time.time() + 3.0
        while time.time() < _warm_deadline:
            if RW.cache_ok(h):
                break
            time.sleep(0.05)
        for attempt in range(10):
            time.sleep(args.delay if attempt == 0 else 0.3)
            battle, info = read_once(h, cards_db, cache_only=True)
            if not battle:
                emit(f"  第{attempt+1}次：{info}")
                continue
            # 用"是否读取失败"判定，而不是"随从数"
            # 空场是合法状态（空场必输），不能因为 0 张就放弃整场
            _mu = bool(battle["player"].get("cards_unknown"))
            _ou = bool(battle["opponent"].get("cards_unknown"))
            if _mu or _ou:
                emit(f"  第{attempt+1}次：读取失败（我方未知={_mu} 对方未知={_ou}）")
                continue
            # ── 阵容是否已经铺开 ──
            # 「立即读取」会在随从还没飞进战场时就读到半空场（实测：我方只读到 1 张，
            # 实际 7 张，于是判成必输，但用户其实在连胜）。
            # 所以如果总随从数太少、或一边有 3+ 张而另一边 0 张，就补读一次取更完整的。
            _nm = len(battle["player"]["board"])
            _no = len(battle["opponent"]["board"])
            # 只处理**明显不对称**（一边 0 张、另一边 3 张以上）。
            # 双方都只有 1~2 张是正常的早期对局，不能因此空转。
            # ⚠️ **一边空场、另一边有兵** 一律当成"还没铺开"，补读。
            #    以前只在一方 0 张、另一方 ≥3 张时补读，于是"对方 0 张"被当成
            #    真·空场 → 判成 100% 必胜（用户实测那把其实是平局/险胜）。
            #    对方空场是极罕见的合法状态，宁可多读几次。
            _looks_partial = ((_nm == 0) != (_no == 0))
            if _looks_partial and rerolled[0] >= 3:
                _looks_partial = False          # 最多补读 3 次
            if _looks_partial:
                rerolled[0] += 1
            if not _looks_partial:
                break
            if _nm + _no > best_total[0]:
                best_total[0] = _nm + _no
                best_battle[0] = battle
            emit(f"  第{attempt+1}次：阵容还没铺开（我方{_nm} 对方{_no}），补读（第{rerolled[0]}次）")
            battle = None
        if best_battle[0] is not None and (not battle or
                len(battle["player"]["board"]) + len(battle["opponent"]["board"]) < best_total[0]):
            battle = best_battle[0]
            emit("  采用补读到的更完整阵容")
        if not battle:
            emit("  放弃本场（读不全，不给错的数）")
            continue

        # ── 整局结束：我方血量 <= 0（被淘汰）──
        # 这条必须在模拟之前判，因为阵亡时通常已经没随从了，模拟也没意义。
        try:
            _hp0 = int(battle["player"].get("raw_hp") or 0)
            if _hp0 <= 0:
                # 名次不在这里读：`_maybe_record_game_over()` 内部会读，
                # 且只在出局后采信一次 —— 这里再读一遍会白扫 30 秒内存。
                # 幂等记录；光环**不清空**（等下一把开始才清）
                _wrote = _maybe_record_game_over(
                    battle["player"].get("hero"), _hp0,
                    battle["player"].get("board") or [], emit)
                if _wrote:
                    try:
                        # 用户要求：光环面板**整局结束也不清**
                        # → 不发 gameOver / gameover 信号，只保留效果列表
                        _feed_write({"phase": "other", "win": None,
                                    "gameOver": False,
                                    "leaderEffects": leader_effects_for_feed(),
                                    "updatedAt": time.strftime("%H:%M:%S")})
                    except Exception:
                        pass
                time.sleep(_sleep_sec(args))
                continue
        except Exception as _e:
            emit(f"  （整局结束判定失败：{_e}）")

        # 双方都空场 → 不是真正的战斗（刚进游戏/结算瞬间），跳过
        _n_me = len(battle["player"]["board"])
        _n_op = len(battle["opponent"]["board"])
        if _n_me == 0 and _n_op == 0:
            emit("  双方都没有随从，跳过本场（不是真正的战斗）")
            try:
                _feed_write({"phase": "operate", "win": None,
                            "leaderEffects": leader_effects_for_feed(),
                            "updatedAt": time.strftime("%H:%M:%S")})
            except Exception:
                pass
            time.sleep(_sleep_sec(args))
            continue

        # 双方都有随从 → 正常模拟
        import damage_dist as DD
        try:
            # 时间预算：普通局 300ms，有「谢幕曲召唤随从」的牌放宽到 1000ms。
            # 用 run_budgeted 在预算内尽量多跑，而不是固定次数。
            _has_summon = DD.has_summon_lastword(battle)
            _budget = 1000 if _has_summon else 300
            _t_sim = time.time()
            raw = DD.run_budgeted(battle, budget_ms=_budget)
            emit(f"  模拟：预算{_budget}ms 实耗{(time.time()-_t_sim)*1000:.0f}ms "
                 f"跑了{raw.get('simulations','?')}次"
                 + ("（含召唤谢幕曲）" if _has_summon else ""))
        except Exception as e:
            # 模拟超时/失败不能让判定器崩掉，跳过这一场继续等下一场
            emit(f"  模拟失败/超时：{type(e).__name__}: {e}")
            time.sleep(_sleep_sec(args))
            continue
        wr = raw.get("winRate") or {}
        if isinstance(wr, dict):
            win = int(round(wr.get("player") or 0))
            loss = int(round(wr.get("opponent") or 0))
            draw = int(round(wr.get("draw") or 0))
        else:
            win = int(round(wr or 0)); loss, draw = 100 - win, 0
        res = {"win": win, "loss": loss, "draw": draw, "damageDist": {}, "detail": ""}
        dmg = raw.get("damage") or {}
        if isinstance(dmg, dict):
            for key in ("toOpponent", "toPlayer"):
                rows = SIM.dist_rows(dmg.get(key))
                if rows:
                    res["damageDist"][key] = rows

        # ⚠️ 必须把**完整明细写进日志**：之前只写了"胜X%败Y%"，
        #    伤害分布只在 stdout，重启就丢了，导致无法和实际掉血对账。
        emit(f"  我方 等级{battle['player']['level']} 血量{battle['player']['hp']} "
             f"随从{len(battle['player']['board'])}张")
        for c in battle["player"]["board"]:
            emit(f"      {c['star']}★ {c['attack']}/{c['health']}  {c['name']}")
        emit(f"  对方 等级{battle['opponent']['level']} 血量{battle['opponent']['hp']} "
             f"随从{len(battle['opponent']['board'])}张")
        for c in battle["opponent"]["board"]:
            emit(f"      {c['star']}★ {c['attack']}/{c['health']}  {c['name']}")
        emit(f"  ★ 胜率 {res['win']}%  败率 {res['loss']}%  平率 {res['draw']}%")
        for _k, _lab in (("toOpponent", "对方掉血"), ("toPlayer", "我方掉血")):
            _rows = (res.get("damageDist") or {}).get(_k) or []
            if _rows:
                emit(f"  {_lab}：" + "  ".join(
                    f"{r['damage']}点 {r['pPercent']:.0f}%" for r in _rows[:10]))
                _lo, _hi = _rows[0]["damage"], _rows[-1]["damage"]
                emit(f"              （范围 {_lo}~{_hi} 点）")
        SIM.render(battle, res)
        SIM.feed_overlay(res)
        # 战斗结果由 SIM 写入，**没有带光环列表** → 这里补上，
        # 否则光环面板会因为「暂无我方效果」而一直隐藏。
        # ⚠️ 必须走 FEED.update()（原子读-改-写）：旧实现是 open(fp,"w") 直接写回，
        #    读者可能读到半截 JSON；而且失败时是 `pass`，一条痕迹都不留。
        try:
            _publish_leader_effects_on_feed()
        except Exception as _e:
            emit(f"  （光环写入悬浮层失败：{_e}）")
        has_result[0] = True
        # 记下这一场的预测与战前血量，等下一次读取时用实际掉血核对
        try:
            _rows0 = ((res or {}).get("damageDist") or {}).get("toPlayer") or []
            _dist = [(int(r.get("damage") or 0), float(r.get("pPercent") or 0))
                     for r in _rows0]
            _wr0 = (res or {}).get("winRate") or {}
            _CALIB[0] = {"hp_before": int(battle["player"].get("raw_hp") or 0),
                         "dist": _dist,
                         "win": _wr0.get("player"),
                         "t": time.strftime("%H:%M:%S")}
        except Exception:
            pass
        _save_calibration_shot(battle, res, emit)
        # ── 整局结束检测：血量归零 = 被淘汰 ──
        try:
            _raw = int(battle["player"].get("raw_hp") or 0)
            if _raw <= 0:
                # 名次由 `_maybe_record_game_over()` 内部统一读一次；
                # 这里不再重复快照（一次约 30s）。
                # 幂等：同一局只记一次；且**不清空光环**
                # （用户要求：光环面板等到下一把开始或关闭插件才清）
                _maybe_record_game_over(battle["player"].get("hero"), _raw,
                                        battle["player"].get("board"), emit)
        except Exception as _e:
            emit(f"  （整局结算失败：{_e}）")

        # 主战者光环：只统计我方，跨战斗累积（战斗结束不清）
        try:
            merge_leader_effects(detect_leader_effects(battle))
        except Exception:
            pass

        _last_result[0] = {"phase": "battle",
                           "win": res.get("win"), "loss": res.get("loss"),
                           "draw": res.get("draw"),
                           "damageDist": res.get("damageDist") or {}}
        _result_at[0] = time.time()      # 开始计时
        try:
            _battle_dur[0] = time.time() - _battle_started_at
        except Exception:
            _battle_dur[0] = 0.0
        emit(f"  锁定：胜{res['win']}% 败{res['loss']}% 平{res['draw']}%")

        # ── 记录到历史（面板读它）────────────────────────────────
        # 「实际掉血」用**上一次记录的血量**和本次开战血量对比得出，
        # 不需要人工输入：上一场我方血量 - 本场我方血量 = 上一场我方掉血
        try:
            prev = (HIST.load().get("records") or [])
            if prev:
                p0 = prev[-1]
                d_me = int(p0.get("me_hp") or 0) - int(battle["player"]["hp"])
                d_op = int(p0.get("op_hp") or 0) - int(battle["opponent"]["hp"])
                if d_me > 0:
                    p0["actual_me"] = d_me
                if d_op > 0:
                    p0["actual_opp"] = d_op
                recs = HIST.load().get("records") or []
                if recs:
                    recs[-1] = p0
                    d = HIST.load()
                    d["records"] = recs
                    HIST._save(d)
            HIST.start_session()
            HIST.append({
                "t": time.strftime("%H:%M:%S"),
                "me_hero": int(battle["player"].get("hero") or 0),
                "op_hero": int(battle["opponent"].get("hero") or 0),
                "me_lv": battle["player"]["level"], "me_hp": battle["player"]["hp"],
                "me_board": battle["player"]["board"],
                "op_lv": battle["opponent"]["level"], "op_hp": battle["opponent"]["hp"],
                "op_board": battle["opponent"]["board"],
                "win": res["win"], "loss": res["loss"], "draw": res["draw"],
            })
        except Exception as _e:
            emit(f"  （历史记录失败: {_e}）")
        done += 1
        if args.once:
            break

        while time.time() - t_start < args.timeout:
            time.sleep(1.0)
            im3 = GRAB.grab_screen()
            if im3 is None:
                continue
            if PHASE.phase_of(im3) != "battle":
                tracker.force("operate", time.time())
                emit("  （战斗结束，等待下一场）")
                break

    emit("")
    emit(f"=== 结束，共处理 {done} 场 ===")
    logf.close()
    print("\n日志:", log_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

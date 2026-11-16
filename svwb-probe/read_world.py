#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
读对局双方状态 —— 走 **Sv2WarchessWorldData**（最可靠的一条路）。

为什么这是最优路线：
  `Sv2WarchessWorldData` 直接持有两个引用：
      Sv2WarchessBattlePlayerBase <Player>k__BackingField     ← 我方
      Sv2WarchessBattlePlayerBase <Opponent>k__BackingField   ← 对方
  字段声明顺序是 Player 在前、Opponent 在后，所以**按偏移从小到大**
  就是「我方 → 对方」，不需要 OCR、不需要猜、不需要按血量对号。

做法：
  1. 定位 Sv2WarchessWorldData 类
  2. 扫描它的实例
  3. 在该实例的 0x10~0x500 里逐个指针字段，检查目标对象的 klass
     是否等于 Sv2WarchessBattlePlayerBase 的类 → 命中就是玩家对象
  4. 按字段偏移排序：第一个 = 我方，第二个 = 对方
  5. 从玩家对象 +0x10 起读 uid/hero/life/grade（**没有护甲**，见下方常量区的说明），再读随从列表

只读（OpenProcess + ReadProcessMemory），不写入/不注入/不 hook。
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from shadow_warchess_reader import (      # noqa: E402
    find_game_pid, open_proc, read, cstr, load_cards, card_name,
    scan_qwords, scan_qwords_near, find_class,
)

HEADER = 0x10          # 对象头：klass@0 + monitor@8

# 玩家对象布局（对象 +0x10 起，已用真值验证）：
#   uid@+0x00(8)  hero@+0x08(4)  life@+0x0c(4)  __@+0x10(4)  grade@+0x14(4)  evolve@+0x18(4)
#
# ⚠️ +0x10 在源码里对应 `Sv2WarchessBattlePlayerBase.Armor`（反编译已核对，字段顺序 =
#    UserId/HeroId/Life/Armor/Grade/EvolvePoint…，偏移就是 +0x10/0x18/0x1c/0x20/0x24）。
#    但影之幻境里它恒为 0（30 条历史日志全是 0），用户也说"主战者没有护甲" → 不读、不校验、
#    不建模（§14.5）。⚠️ 槽位必须留着：挪了后面的 grade(+0x14) 就全错。


WD_PID = [None]


WDCACHE = os.path.join(HERE, "logs", "_world_cache.json")


def _cache_load():
    try:
        import json as _j
        with open(WDCACHE, encoding="utf-8") as fh:
            return _j.load(fh)
    except Exception:
        return {}


def _cache_save(d):
    # 原子写：判定器主线程/后台扫描线程都会落地缓存，读到半截会让下次冷扫描
    try:
        import json as _j
        os.makedirs(os.path.dirname(WDCACHE), exist_ok=True)
        tmp = "%s.tmp-%d" % (WDCACHE, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            _j.dump(d, fh, ensure_ascii=False)
        os.replace(tmp, WDCACHE)
    except Exception:
        pass


def klass_of(h, addr):
    raw = read(h, addr, 8)
    return struct.unpack("<Q", raw)[0] if raw and len(raw) == 8 else 0


WD_OPP_UID_OFF = 0x118     # WorldData 里对方的 uid（子代理验证过）


def _read_opp_uid(h, wd_addr):
    """读 WorldData+0x118 的对方 uid；读不到返回 None"""
    try:
        d = read(h, wd_addr + WD_OPP_UID_OFF, 8)
        if d and len(d) == 8:
            v = struct.unpack("<q", d)[0]
            if v > 1000000:
                return v
    except Exception:
        pass
    return None


def _assign_sides(players, opp_uid):
    """
    用对方 uid 给玩家标身份。

    比起靠引用偏移（+0x190/+0x198）猜，uid 匹配是**确定性的**：
    谁的 uid 等于 WorldData+0x118 谁就是对方。
    读不到 opp_uid 时退回原来的偏移顺序。
    """
    if not players:
        return
    if opp_uid:
        for p in players:
            if p.get("uid") == opp_uid:
                p["side"] = "对方"
        for p in players:
            if p.get("side") not in ("我方", "对方"):
                p["side"] = "我方"
        # 极端情况：两个都等于 opp_uid（不可能）→ 保持第一个为对方
        return
    for i, p in enumerate(players):
        p["side"] = "我方" if i == 0 else "对方"


def read_player_obj(h, addr):
    """从玩家对象 +0x10 按声明顺序读字段（已用真值验证过布局）"""
    raw = read(h, addr + HEADER, 0x30)
    if not raw or len(raw) < 0x20:
        return None
    uid = struct.unpack_from("<Q", raw, 0x00)[0]
    hero = struct.unpack_from("<i", raw, 0x08)[0]
    life = struct.unpack_from("<i", raw, 0x0C)[0]
    grade = struct.unpack_from("<i", raw, 0x14)[0]
    evo = struct.unpack_from("<i", raw, 0x18)[0]
    # 进化点相关字段（2026-10-18）：按 Sv2WarchessBattlePlayerBase 的**声明顺序**读。
    # 已验证 0x00..0x18（uid/hero/life/grade/evo）与声明完全一致，后面继续按序：
    #   +0x1c EvolveUnlocked  +0x20 SuperEvolveUnlocked  +0x24 MaxEvolvePoint
    #   +0x28 SuperEvolvePoint +0x2c MaxSuperEvolvePoint  +0x34 HandCardNum
    # 用途：本局"已经用掉的进化点" = (MaxEvolvePoint + MaxSuperEvolvePoint) - (EvolvePoint + SuperEvolvePoint)，
    # 这样**判定器启动前就进化过的**次数也能算进来（用户指出的漏算）。
    ev_unlocked = struct.unpack_from("<i", raw, 0x1c)[0]
    sp_unlocked = struct.unpack_from("<i", raw, 0x20)[0]
    max_evo = struct.unpack_from("<i", raw, 0x24)[0]
    sp_evo = struct.unpack_from("<i", raw, 0x28)[0]
    max_sp = struct.unpack_from("<i", raw, 0x2c)[0]
    # ⚠️ hero_id 范围不能假设是 1000~10000！
    #    实测**我方**主战者是 100000（特殊/变身形态），之前的窄范围
    #    把玩家自己判成无效对象，导致每场都"只读到一方玩家"直接放弃，
    #    表现为胜率永远不显示。
    if not (10 <= hero <= 999999):
        return None
    # ⚠️ 校验不能太严：玩家**已阵亡/已离开**时 life 会是 0 或负数、
    #    grade 可能为 0，之前用 `1 <= grade <= 8` 会把他整个丢掉，
    #    结果只读到一方（表现为"没读到双方玩家"）。
    if not (-20 <= life <= 99 and 0 <= grade <= 8):
        return None
    # ⚠️ 还要校验 uid：战斗结束后世界里会残留一些对象，
    #    hero/life/grade 恰好能过上面的范围检查（实测读到 life=0、grade=1 的残留体），
    #    但它们 uid 是 0，被当成玩家会让只读到一方变成误判。
    #    真实账号 uid 都是 9 位以上。
    if uid < 1000000:
        return None
    data = {"obj": hex(addr), "uid": uid, "hero_id": hero, "life": life,
            "grade": grade, "evolve_point": evo}
    # 只采信合理范围（防止布局变化时把别的字段当进化点）
    if 0 <= max_evo <= 20 and 0 <= sp_evo <= 20 and 0 <= max_sp <= 20:
        data.update(evolve_unlocked=ev_unlocked, super_unlocked=sp_unlocked,
                    max_evolve_point=max_evo, super_evolve_point=sp_evo,
                    max_super_evolve_point=max_sp)
    return data


def read_list_objects(h, list_ptr, maxn=12):
    """List<T>：_items@+0x10, _size@+0x18；数组数据@+0x20"""
    if not list_ptr or list_ptr < 0x10000:
        return None
    head = read(h, list_ptr, 0x28)
    if not head or len(head) < 0x20:
        return None
    items = struct.unpack_from("<Q", head, 0x10)[0]
    size = struct.unpack_from("<i", head, 0x18)[0]
    if not (0 <= size <= 64) or items < 0x10000:
        return None
    if size == 0:
        return []
    out = []
    arr = read(h, items + 0x18, 8)
    if not arr:
        return None
    n = struct.unpack("<Q", arr)[0]
    n = min(n, size, maxn)
    for i in range(n):
        d = read(h, items + 0x20 + i * 8, 8)
        if not d:
            break
        p = struct.unpack("<Q", d)[0]
        if p > 0x10000:
            out.append(p)
    return out


# Sv2WarchessBattleCardBase 的字段偏移（**已用画面真值反解确认**）
#   实例 0x1d9d4c25ee0 画面显示 33/14 → decl 布局解出 atk=+0x3c、life=+0x40，命中 2/2
#   按声明顺序：Star=+0x38、Atk=+0x3c、Life=+0x40（**先攻后血**，用户 2026-10-18 确认）
#   `read_card()` 从 +0x38 一次解 `<iii>` = star/atk/life，所以下面只留 Star 常量。
#   ⚠️ 曾经这里还有 CARD_ATK_OFF / CARD_LIFE_OFF 两个常量，**没人引用**；
#      已删掉，免得下一个人以为"改常量就能改攻血语义"（实际要改 read_card）。
CARD_STAR_OFF = 0x38
CARD_BUILDINFO_OFF = 0x10  # <buildInfo>（里面 +0x10 是 CardId）
CARD_PARAM_OFF = 0x18      # <BaseParameter>（后备路径）


def read_card(h, obj):
    """
    按**固定偏移**读一张场上随从。

    ⚠️ 这里曾经用"扫描连续三元组 (star, atk, life)"的启发式，
    结果随机命中 BaseParameter（基础值）而不是当前值：
    实测 6 张里有 2 张完全读错（画面 33/14 读成 1/2、46/37 读成 2/6）。
    现在改用反解确认过的固定偏移，不再猜。
    """
    raw = read(h, obj + CARD_STAR_OFF, 0x10)
    if not raw or len(raw) < 12:
        return None
    st, atk, lf = struct.unpack_from("<iii", raw, 0)
    # 合理性校验（不满足说明这个对象不是场上随从）
    if not (0 <= st <= 9):
        return None
    if not (0 <= atk <= 999):
        return None
    if not (-99 <= lf <= 999):
        return None

    card = {"obj": hex(obj), "star": st, "atk": atk, "life": lf,
            "card_id": None, "name": None}
    # 进化状态（升级形态）：按"base 类 4 个 int 之后"的布局猜的位置：
    #   +0x30 isEvolve(int)  +0x34 evolveState(int)
    # ⚠️ 这两个偏移**只有推断、还没实测校准**：值不合理就不采信（下面 watch_mem 会退回按卡号判断形态）。
    try:
        ev_raw = read(h, obj + 0x30, 8)
        if ev_raw and len(ev_raw) == 8:
            is_ev = struct.unpack_from("<i", ev_raw, 0)[0]
            ev_st = struct.unpack_from("<i", ev_raw, 4)[0]
            if is_ev in (0, 1) and 0 <= ev_st <= 3:
                card["is_evolve"] = is_ev
                card["evolve_state"] = ev_st
            # +0x34 的**原始**值：贝鲁这类卡会把「X」写在这里（实测 = 本局进化数）
            if 0 <= ev_st <= 60:
                card["evolve_value"] = ev_st
    except Exception:
        pass

    # ---- 卡 ID ----
    # ① 精确路径：<buildInfo>(+0x10) 的 +0x10 就是 CardId
    #    （子代理从元数据+真机确认：[[card+0x10]+0x10]）
    d = read(h, obj + CARD_BUILDINFO_OFF, 8)
    if d:
        bi = struct.unpack("<Q", d)[0]
        if 0x10000 < bi < 0x7FFFFFFFFFFF:
            cid = read(h, bi + 0x10, 4)
            if cid and len(cid) == 4:
                v = struct.unpack("<i", cid)[0]
                if 10000000 <= v <= 99999999:
                    card["card_id"] = v
                    return card

    # ② 后备路径：<BaseParameter>(+0x18) 里搜 8 位数
    d = read(h, obj + CARD_PARAM_OFF, 8)
    if d:
        p = struct.unpack("<Q", d)[0]
        if 0x10000 < p < 0x7FFFFFFFFFFF:
            prm = read(h, p + HEADER, 0x60)
            if prm and len(prm) >= 0x10:
                pv = struct.unpack("<%di" % (len(prm) // 4), prm[:len(prm) // 4 * 4])
                for v in pv:
                    if 10000000 <= v <= 99999999:
                        card["card_id"] = v
                        break
    return card


# Sv2WarchessBattlePlayerBase 的列表字段偏移
# （由**元数据字段声明顺序**算出，且 Life=+0x1c / Grade=+0x24 与截图真值反解一致）
SHOP_CARDS_OFF = 0x50     # <ShopCardList>   商店
HAND_CARDS_OFF = 0x58     # <HandCards>      手牌
BATTLE_CARDS_OFF = 0x60   # <BattleCards>    场上随从  ★这才是战场
# 同结构的其它列表（目前没有代码读，仅记录，别再当常量供着）：
#   <HeroSkill> +0x68   <SkillCards> +0x70   <SpecialCards> +0x78


def read_board(h, player_obj, player_class):
    """
    读该玩家**场上随从**。

    ⚠️ 返回 (found_list, cards, offset)：
       found_list=False 表示根本没找到任何"像卡牌列表"的字段 → 读取失败，
       不能据此判断空场（之前踩过：读失败当成空场，直接判必胜）。

    为什么不再靠"扫描取卡最多"：
       旧实现对 0x10..0x130 全扫，同分时先命中低偏移 —— 而 +0x50 是
       **ShopCardList（商店）**、+0x58 是 HandCards（手牌）。
       元数据确认战场是 **+0x60**，所以现在直接按偏移读，
       只有读不到时才退回扫描，并且给商店/手牌降权。
    """
    # ---- ① 主路径：按元数据确认的偏移直接读 ----
    d = read(h, player_obj + BATTLE_CARDS_OFF, 8)
    if d:
        p = struct.unpack("<Q", d)[0]
        if 0x10000 < p < 0x7FFFFFFFFFFF:
            objs = read_list_objects(h, p)
            if objs is not None:
                cards = [c for c in (read_card(h, o) for o in objs) if c]
                return True, cards, BATTLE_CARDS_OFF

    # ---- ② 回退：扫描（保持兼容，但排除已知非战场列表）----
    best = None
    found_any = False
    for off in range(HEADER, 0x130, 8):
        if off in (SHOP_CARDS_OFF, HAND_CARDS_OFF):
            continue
        d = read(h, player_obj + off, 8)
        if not d:
            continue
        p = struct.unpack("<Q", d)[0]
        if p < 0x10000 or p > 0x7FFFFFFFFFFF:
            continue
        objs = read_list_objects(h, p)
        if objs is None:
            continue
        found_any = True
        cards = [c for c in (read_card(h, o) for o in objs) if c]
        if cards:
            scored = sum(1 for c in cards if c.get("card_id"))
            if best is None or scored > best[0]:
                best = (scored, off, cards)
    if best is None:
        return found_any, [], None
    return True, best[2], best[1]


# WorldData 里战斗结果 DTO 的偏移（子代理用元数据+真机确认）
WD_CACHE_BATTLE_OFF = 0x128   # <CacheBattleResult>
DTO_MINIONS_OFF = 0x40        # S_COMBAT_OPPONENT.<minions>
DTO_SELF_MINIONS_OFF = 0x50   # <self_minions>
CM_CARD_ID_OFF = 0x1C         # CARD_MINIONS.<card_id>


def read_minions_dto(h, wd_addr):
    """
    从 WorldData + 0x128 -> S_COMBAT_OPPONENT(+0x40/+0x50) 读双方随从。

    ⚠️ 这里有两个坑（都踩过）：
      1. CARD_MINIONS 继承 Netease.GameNet.ProtoBase（8 字节），
         所以所有 DTO 字段偏移要**比只算自身字段多 8**；
      2. minions 是 **Il2CppArray<T>** 不是 List<T>：
         ounds@+0x10、max_length@+0x18（8 字节）、数据 @+0x20。

    只拿得到 card_id（星级按卡表补），所以是**后备路径** ——
    场上对象能读到时优先用对象（有当前攻血，含增益）。

    返回 (对方列表, 我方列表)，元素形如 {card_id:…, star:…}；读不到返回 (None, None)。
    """
    cache = 0
    d = read(h, wd_addr + WD_CACHE_BATTLE_OFF, 8)
    if d:
        cache = struct.unpack("<Q", d)[0]
    if not (0x10000 < cache < 0x7FFFFFFFFFFF):
        return None, None

    def read_arr(off):
        d = read(h, cache + off, 8)
        if not d:
            return None
        p = struct.unpack("<Q", d)[0]
        if not (0x10000 < p < 0x7FFFFFFFFFFF):
            return None
        head = read(h, p + 0x10, 0x10)
        if not head or len(head) < 0x10:
            return None
        n = struct.unpack_from("<q", head, 0x8)[0]
        if not (0 <= n <= 12):
            return None
        data = read(h, p + 0x20, 8 * n)
        if not data or len(data) < 8 * n:
            return None
        out = []
        for i in range(n):
            e = struct.unpack_from("<Q", data, 8 * i)[0]
            if not (0x10000 < e < 0x7FFFFFFFFFFF):
                continue
            cd = read(h, e + CM_CARD_ID_OFF, 4)
            if not cd or len(cd) < 4:
                continue
            cid = struct.unpack("<i", cd)[0]
            if 10000000 <= cid <= 99999999:
                out.append({"card_id": cid, "star": 0})
        return out or None

    return read_arr(DTO_MINIONS_OFF), read_arr(DTO_SELF_MINIONS_OFF)


def read_player_dto(h, wd_addr):
    """
    从 `WorldData + 0x128`（S_COMBAT_OPPONENT）读**对方玩家**。

    为什么需要：开战瞬间 `WorldData` 的对方玩家引用（+0x198）经常是空的，
    于是 snapshot 只能读到一方 → 整场被放弃（日志里的"只读到一方玩家"）。
    但服务端下发的 DTO 此时已经有完整数据。

    DTO 布局（子代理用元数据+真机确认，与 BattlePlayerBase 同序）：
        +0x10 uid  +0x18 hero  +0x1c life  +0x24 grade
        （+0x20 槽位不用：主战者**没有护甲**，见 read_player_obj 的说明）
        +0x40 minions（Il2CppArray<CARD_MINIONS>，元素 +0x1c 是 card_id）
    """
    cache = 0
    d = read(h, wd_addr + WD_CACHE_BATTLE_OFF, 8)
    if d:
        cache = struct.unpack("<Q", d)[0]
    if not (0x10000 < cache < 0x7FFFFFFFFFFF):
        return None
    raw = read(h, cache + HEADER, 0x28)
    if not raw or len(raw) < 0x28:
        return None
    uid = struct.unpack_from("<q", raw, 0x00)[0]
    hero = struct.unpack_from("<i", raw, 0x08)[0]
    life = struct.unpack_from("<i", raw, 0x0C)[0]
    grade = struct.unpack_from("<i", raw, 0x14)[0]
    if not (10 <= hero <= 999999 and -20 <= life <= 99 and 0 <= grade <= 8):
        return None
    p = {"addr": hex(cache), "uid": uid, "hero_id": hero, "life": life,
         "grade": grade, "side": "对方",
         "ref_off": "dto", "from_dto": True, "cards": []}
    cards = read_minions_dto(h, wd_addr)[0] or []
    p["cards"] = cards
    p["cards_unknown"] = (len(cards) == 0)
    return p


def read_from_combat_result(h, wd_addr):
    """
    从 WorldData.<CacheBattleResult>（S_COMBAT_OPPONENT）读对方/我方场上随从。

    返回 (opp_cards, self_cards)；读不到则返回 (None, None)。
    """
    cr_cls = find_class(h, "S_COMBAT_OPPONENT", "Netease.GameNet.MOS_PROTO")
    if isinstance(cr_cls, tuple):
        cr_cls = cr_cls[0]
    if not cr_cls:
        return None, None

    # 在 WorldData 里找指向 S_COMBAT_OPPONENT 的字段
    target = None
    for off in range(HEADER, 0x500, 8):
        d = read(h, wd_addr + off, 8)
        if not d:
            continue
        p = struct.unpack("<Q", d)[0]
        if p < 0x10000 or p > 0x7FFFFFFFFFFF:
            continue
        if klass_of(h, p) == cr_cls:
            target = p
            break
    if not target:
        return None, None

    # 在该对象里找所有"能解析出卡牌"的列表
    found = []
    for off in range(HEADER, 0x200, 8):
        d = read(h, target + off, 8)
        if not d:
            continue
        p = struct.unpack("<Q", d)[0]
        if p < 0x10000 or p > 0x7FFFFFFFFFFF:
            continue
        objs = read_list_objects(h, p)
        if not objs:
            continue
        cards = [c for c in (read_card(h, o) for o in objs) if c]
        if cards:
            found.append((off, cards))
    if not found:
        return None, None

    # S_COMBAT_OPPONENT 的字段顺序：minions 在前、self_minions 在后
    # → 偏移较小的视为对方，较大的视为我方
    found.sort(key=lambda t: t[0])
    opp = found[0][1]
    me = found[1][1] if len(found) > 1 else None
    return opp, me


_LAST_INSTANCE = {}


def invalidate(pid=None):
    """
    让缓存失效：下次 snapshot 会重新扫描世界对象。

    为什么要手动失效：缓存里存的是**上一局**的 WorldData 实例，
    而新一局游戏会创建新的世界对象。`cache_ok` 只检查"有一方还活着"，
    旧世界里对方往往还活着（我们在上一局已被淘汰），于是永远判定"缓存有效"，
    整局都读到上一局的死数据（用户反馈：正在进行的游戏读不到、战绩一直不推进）。
    """
    try:
        key = str(pid if pid is not None else WD_PID[0])
        cache = _cache_load() or {}
        if key in cache:
            cache.pop(key, None)
            _cache_save(cache)
            return True
    except Exception:
        pass
    return False


def cache_ok(h):
    """
    廉价检查：缓存的 WorldData 实例是否仍然是**当前这一局**。

    只读 3 次内存（实例 klass + 两个玩家引用），microsecond 级，
    可以放心在判定主循环里每轮调用。

    判据：实例 klass 对得上，且两个引用里**至少有一方活着**（life>=1）。
    """
    try:
        ent = (_cache_load() or {}).get(str(WD_PID[0])) or {}
        if not ent.get("wd_instance") or not ent.get("wd_class"):
            return False
        a0 = int(ent["wd_instance"], 16)
        wd_cls0 = int(ent["wd_class"], 16)
        if klass_of(h, a0) != wd_cls0:
            return False
        for off in (0x190, 0x198):
            d = read(h, a0 + off, 8)
            if not d:
                continue
            p = struct.unpack("<Q", d)[0]
            if not (0x10000 < p < 0x7FFFFFFFFFFF):
                continue
            po = read_player_obj(h, p)
            if po and po.get("life", 0) >= 1:
                return True
        return False
    except Exception:
        return False


def prewarm(h, cards_db):
    """
    预热：确保缓存的 WorldData 实例是**当前这一局**的。

    在运营阶段调用（不急），避免开战那一刻才去扫 78 秒内存。
    返回 True 表示缓存可用。
    """
    try:
        snap = snapshot(h, cards_db)
        if "error" in snap:
            return False
        return bool(snap.get("cached")) or bool(snap.get("players"))
    except Exception:
        return False


def snapshot(h, cards_db, force_cold=None):
    # ── 先试缓存（同一进程内类地址与实例地址都固定）──
    mt = os.path.getmtime(os.path.join(HERE, "read_world.py")) if os.path.isfile(
        os.path.join(HERE, "read_world.py")) else 0
    cache = _cache_load()
    ent = cache.get(str(WD_PID[0])) or {}
    if ent.get("wd_instance") and ent.get("wd_class"):
        a0 = int(ent["wd_instance"], 16)
        wd_cls0 = int(ent["wd_class"], 16)
        # ① 先校验这个实例还是不是 WorldData（一次读，纳秒级）
        if klass_of(h, a0) == wd_cls0:
            got = []
            for off in (0x190, 0x198):
                d = read(h, a0 + off, 8)
                if not d:
                    continue
                p = struct.unpack("<Q", d)[0]
                if p < 0x10000 or p > 0x7FFFFFFFFFFF:
                    continue
                if read_player_obj(h, p):
                    got.append((off, p))
            # ② 缓存实例可能是**上一局残留**（人已阵亡、血量负数），
            #    必须要求至少一方活着，才算这一局仍然有效。
            _alive = []
            for _off, _pa in got:
                _p = read_player_obj(h, _pa)
                if _p and _p.get("life", 0) >= 1:
                    _alive.append((_off, _pa))
            # ⚠️ 以前这里 got = _alive，会把**已阵亡的一方直接丢掉**。
            #    结果是：玩家血量归零（整局结束）时只读到对方 →
            #    日志刷「只读到一方玩家」→ 整场放弃 → 战绩不记录。
            #    现在改成：只要**还有一方活着**就保留两边的数据，
            #    死亡方标记 dead=True，让上层能识别「整局结束」。
            if _alive:
                got = got
            if len(got) >= 1 and _alive:
                out = {"cached": True, "used_fixed_offsets": True,
                       "wd_instance": hex(a0), "wd_class": ent.get("wd_class"),
                       "wd_instances": -1,
                       "player_refs": [hex(o) for o, _ in got],
                       "players": []}
                for idx, (off, paddr) in enumerate(got[:2]):
                    p = read_player_obj(h, paddr)
                    if not p:
                        continue
                    p["ref_off"] = hex(off)
                    p["dead"] = int(p.get("life") or 0) <= 0
                    bl = read_board(h, paddr, 0)
                    cs = bl[1] if isinstance(bl, tuple) and len(bl) >= 2 else []
                    for c in cs:
                        if c.get("card_id"):
                            c["name"] = card_name(cards_db, c["card_id"])
                    p["cards"] = cs
                    p["cards_unknown"] = False   # 缓存分支能读到列表，空就是真空
                    out["players"].append(p)

                # ③ 统一按 uid 判定身份
                _assign_sides(out["players"], _read_opp_uid(h, a0))

                # ④ 只有一方 → 用 DTO 补对方（开战瞬间的常态）
                if (len(out["players"]) == 1
                        and out["players"][0].get("side") == "我方"):
                    try:
                        dto = read_player_dto(h, a0)
                    except Exception:
                        dto = None
                    if dto:
                        for c in dto.get("cards") or []:
                            if c.get("card_id"):
                                c["name"] = card_name(cards_db, c["card_id"])
                        out["players"].append(dto)
                        out["opponent_from_dto"] = True

                # ⑤ 对方场上为空 → 用 DTO 的 minions 补
                #    （缓存分支会提前 return，所以这里必须单独补一次）
                try:
                    _opp = next((x for x in out["players"]
                                 if x.get("side") == "对方"), None)
                    if _opp is not None and not _opp.get("cards"):
                        _dm = read_minions_dto(h, a0)[0]
                        if _dm:
                            _db = cards_db if isinstance(cards_db, dict) else None
                            for c in _dm:
                                cid = c.get("card_id")
                                if cid and _db:
                                    info = _db.get(str(cid)) or {}
                                    if not c.get("star"):
                                        c["star"] = int(info.get("star") or 1)
                                    if not c.get("atk"):
                                        c["atk"] = int(info.get("attack") or 0)
                                    if not c.get("life"):
                                        c["life"] = int(info.get("health") or 1)
                                if cid:
                                    c["name"] = card_name(cards_db, cid)
                            _opp["cards"] = _dm
                            _opp["cards_unknown"] = False
                            _opp["from_dto"] = True
                except Exception:
                    pass
                return out

    # force_cold=False → **只读缓存**，缓存失效立刻返回（不扫内存）。
    # 判定主循环在开战瞬间必须用这个：一次冷扫描 20~40 秒，会把整场战斗错过。
    if force_cold is False:
        return {"error": "cache-miss"}

    wd_cls = find_class(h, "Sv2WarchessWorldData", "Sv2Warchess")
    if isinstance(wd_cls, tuple):
        wd_cls = wd_cls[0]
    if not wd_cls:
        return {"error": "找不到 Sv2WarchessWorldData 类"}

    out = {"wd_class": hex(wd_cls)}

    # ⚠️ 快速重定位：先只扫**上一局实例地址附近**的内存。
    #    新一局的世界对象通常还在同一个大堆段里，实测 0.5 秒就能命中，
    #    而全量扫描 6.4 GB 要 7~11 秒（用户反馈"读取速度太慢"就是这个）。
    #    附近没找到时再退回全量扫描，行为与以前一致。
    sites = []
    _hint = (ent.get("wd_instance") or ent.get("wd_hint")
             or _LAST_INSTANCE.get(str(WD_PID[0])))
    if _hint:
        # 由近及远分批扫：命中就停，避免"附近扫"也把 1~2 GB 全读一遍
        for _win in (16 << 20, 64 << 20, 256 << 20):
            try:
                sites = scan_qwords_near(h, {wd_cls}, int(_hint, 16),
                                         window=_win)
            except Exception:
                sites = []
            if sites:
                out["fast_locate"] = True
                out["fast_window_mb"] = _win >> 20
                break
    if not sites:
        sites = scan_qwords(h, {wd_cls}, threads=8)
    out["wd_instances"] = len(sites)

    # ⚠️ 加速：<Player>/<Opponent> 的字段偏移**实测固定**是 0x190/0x198，
    #    所以优先直接用固定偏移取，并用"字段是否合理"来验证，
    #    这样就不必再做一次全内存扫描去找 BattlePlayerBase 类（省 ~20 秒）。
    FIXED = (0x190, 0x198)
    # ⚠️ 候选实例可能有几百个（旧局的 WorldData 对象还留在堆里）。
    #    以前取**第一个**验证通过的，结果经常拿到上一局的对象 →
    #    整局都在读旧数据（用户反馈"胜率不显示/一直读取中"）。
    #    现在把所有候选都验证一遍，优先选「我方还活着」的那个世界。
    cands = []
    for a in sites:
        got = []
        for off in FIXED:
            d = read(h, a + off, 8)
            if not d:
                continue
            p = struct.unpack("<Q", d)[0]
            if p < 0x10000 or p > 0x7FFFFFFFFFFF:
                continue
            po = read_player_obj(h, p)
            if po:
                got.append((off, p, po))
        if len(got) == 2:
            cands.append((a, [(o, p) for o, p, _po in got],
                          [po for _o, _p, po in got]))

    best = None
    if cands:
        out["used_fixed_offsets"] = True
        OUR_UID = 907732999798
        # ① 我方在场且存活 → 一定是当前这局
        for a, refs, pos in cands:
            if any(po.get("uid") == OUR_UID and int(po.get("life") or 0) > 0
                   for po in pos):
                best = (a, refs)
                break
        # ② 兜底：选双方生命值之和最大的（当前这局通常比残留的死局更"满"）
        if best is None:
            best = max(
                ((a, refs) for a, refs, _pos in cands),
                key=lambda c: sum(
                    int((read_player_obj(h, p) or {}).get("life") or 0)
                    for _o, p in c[1]))
    pl_cls = 0
    if best is None:
        # 固定偏移不可用（版本变了 / 结构不同）→ 退回全扫描
        out["used_fixed_offsets"] = False
        r = find_class(h, "Sv2WarchessBattlePlayerBase", "Sv2Warchess")
        pl_cls = r[0] if isinstance(r, tuple) else r
        for a in sites:
            found = []
            for off in range(HEADER, 0x500, 8):
                d = read(h, a + off, 8)
                if not d:
                    continue
                p = struct.unpack("<Q", d)[0]
                if p < 0x10000 or p > 0x7FFFFFFFFFFF:
                    continue
                if klass_of(h, p) == pl_cls:
                    found.append((off, p))
            if len(found) >= 2 and (best is None or len(found) > len(best[1])):
                best = (a, found)

    if not best:
        out["error"] = "WorldData 里没找到两个玩家引用（可能不在对局）"
        return out

    a, found = best
    found.sort(key=lambda t: t[0])
    try:
        _LAST_INSTANCE[str(WD_PID[0])] = hex(a)
        cache[str(WD_PID[0])] = {"wd_class": hex(wd_cls), "wd_instance": hex(a)}
        _cache_save(cache)
    except Exception:
        pass
    out["wd_instance"] = hex(a)
    out["player_refs"] = [hex(o) for o, _ in found]
    out["players"] = []
    for idx, (off, paddr) in enumerate(found[:2]):
        p = read_player_obj(h, paddr)
        if not p:
            continue
        p["ref_off"] = hex(off)
        bl = read_board(h, paddr, pl_cls)
        found_list = bool(bl[0]) if isinstance(bl, tuple) else False
        if isinstance(bl, tuple) and len(bl) >= 2:
            cs = bl[1]
            for c in cs:
                if c.get("card_id"):
                    c["name"] = card_name(cards_db, c["card_id"])
            p["cards"] = cs
            if len(bl) >= 3:
                p["cards_off"] = hex(bl[2])
        else:
            p["cards"] = []
        # 没找到任何列表 → 读失败，不是空场（关键区分）
        # 读到 0 张时无法区分"真空场"和"没读到"，一律标为不可信，
        # 避免据此判出"对方空场 → 必胜"的假结论
        # ⚠️ 只把"没找到列表"算读失败。
        #    "找到了列表但它是空的"是**合法状态**（空场=必输），
        #    之前这两种情况被混在一起，导致空场被当成读不全而放弃整场。
        p["cards_unknown"] = not found_list
        out["players"].append(p)

    # ★ 兜底：如果**对方**没读到随从，从 WorldData.CacheBattleResult 里补。
    #   实测 WorldData.<Opponent> 的列表经常是空的，而服务端下发的
    #   S_COMBAT_OPPONENT 里带着完整阵容。
    opp = next((p for p in out["players"] if p.get("side") == "对方"), None)
    if opp is not None and not opp.get("cards"):
        # 先用**确认过偏移**的 DTO 路径，失败再退回启发式扫描
        try:
            _oc, _mc = read_minions_dto(h, a)
        except Exception:
            _oc, _mc = None, None
        if not _oc:
            try:
                _oc, _mc = read_from_combat_result(h, a)
            except Exception:
                _oc, _mc = None, None
        if _oc:
            # DTO 只有 card_id，星级/基础攻血按卡表补
            # load_cards() 返回的就是 {卡ID字符串: 卡牌} 的扁平字典
            _db = cards_db if isinstance(cards_db, dict) else None
            for c in _oc:
                cid = c.get("card_id")
                if cid and _db:
                    info = _db.get(str(cid)) or {}
                    if not c.get("star"):
                        c["star"] = int(info.get("star") or 1)
                    c.setdefault("atk", int(info.get("attack") or 0))
                    c.setdefault("life", int(info.get("health") or 1))
                if cid:
                    c["name"] = card_name(cards_db, cid)
            opp["cards"] = _oc
            opp["cards_unknown"] = False
            opp["from_combat_result"] = True
            # 我方若也读不到，用 self_minions 补
            me = next((p for p in out["players"] if p.get("side") == "我方"), None)
            if me is not None and not me.get("cards") and _mc:
                for c in _mc:
                    if c.get("card_id"):
                        c["name"] = card_name(cards_db, c["card_id"])
                me["cards"] = _mc
                me["cards_unknown"] = False
                me["from_combat_result"] = True
    # 统一按 uid 判定身份（+0x190/+0x198 的顺序会随先后手变化，不可靠）
    try:
        _assign_sides(out.get("players") or [], _read_opp_uid(h, a))
    except Exception:
        pass

    # ---- 对方场上为空 → 用 DTO 补 ----
    # 世界数据里对方的 BattleCards 经常是 0 张，真正的阵容在
    # WorldData+0x128 的 S_COMBAT_OPPONENT.minions 里。
    # 不补的话会被当成"对方空场"，直接判我方必胜，胜率完全失真。
    try:
        _opp = next((x for x in (out.get("players") or [])
                     if x.get("side") == "对方"), None)
        if _opp is not None and not _opp.get("cards"):
            _dto_min = None
            try:
                _dto_min = read_minions_dto(h, a)[0]
            except Exception:
                _dto_min = None
            if _dto_min:
                _db = cards_db if isinstance(cards_db, dict) else None
                for c in _dto_min:
                    cid = c.get("card_id")
                    if cid and _db:
                        info = _db.get(str(cid)) or {}
                        if not c.get("star"):
                            c["star"] = int(info.get("star") or 1)
                        if not c.get("atk"):
                            c["atk"] = int(info.get("attack") or 0)
                        if not c.get("life"):
                            c["life"] = int(info.get("health") or 1)
                    if cid:
                        c["name"] = card_name(cards_db, cid)
                _opp["cards"] = _dto_min
                _opp["cards_unknown"] = False
                _opp["from_dto"] = True
    except Exception:
        pass

    # ---- 只读到一方时，用 DTO 补对方 ----
    # 开战瞬间 +0x198（对方引用）经常是空的，此时直接放弃会让胜率完全不显示。
    try:
        _ps = out.get("players") or []
        if len(_ps) == 1 and _ps[0].get("side") == "我方":
            _dto = read_player_dto(h, a)
            if _dto:
                for c in _dto.get("cards") or []:
                    if c.get("card_id"):
                        c["name"] = card_name(cards_db, c["card_id"])
                out["players"].append(_dto)
                out["opponent_from_dto"] = True
    except Exception:
        pass

    return out


def render(res):
    if "error" in res:
        print(f"[!] {res['error']}")
        for k, v in res.items():
            if k != "error":
                print(f"    {k} = {v}")
        return
    print(f"WorldData 实例 {res['wd_instances']} 个，选中 {res['wd_instance']}")
    print(f"玩家引用字段: {res['player_refs']}")
    for p in res["players"]:
        print("")
        print(f"  [{p['side']}] {p['obj']}  引用偏移={p['ref_off']}")
        # ⚠️ get()：对方走 DTO 那条路时**没有 evolve_point** 这个键
        #    （旧代码写死 p['evolve_point']，一旦对方来自 DTO 就 KeyError）
        print(f"      英雄={p['hero_id']}  血量={p['life']} "
              f"★等级={p['grade']}  进化点={p.get('evolve_point', '?')}")
        print(f"      场上随从 {len(p['cards'])} 张（列表偏移 {p.get('cards_off')}）:")
        for c in p["cards"]:
            print(f"        {c['star']}★ {c['atk']}/{c['life']}  "
                  f"id={c.get('card_id')}  {c.get('name') or '?'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-phase", action="store_true", help="跳过阶段判别（调试用）")
    args = ap.parse_args()
    pid = args.pid or find_game_pid()[0]
    if not pid:
        print("找不到游戏进程")
        return 1
    h = open_proc(pid)
    cards_db = load_cards()

    # ★ 阶段门控：运营阶段不读（用户要求）。
    if not args.no_phase:
        try:
            import grab as GRAB
            import phase as PHASE
            img = GRAB.grab_screen()
            if img is not None:
                ph = PHASE.phase_of(img)
                if ph != "battle":
                    print(f"【{'运营阶段' if ph == 'operate' else '不在对局'}】跳过读取")
                    return 0
        except Exception as e:
            print(f"（阶段判别失败，继续读取: {e}）", file=sys.stderr)
    print(f"# pid={pid} 卡片 {len(cards_db)} 张（只读）\n", file=sys.stderr)
    res = snapshot(h, cards_db)
    if args.json:
        import json
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        render(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())

def read_card_id(h, obj):
    """只读一张卡的 CardId（buildInfo +0x10），不要求 star/atk 合理。"""
    try:
        d = read(h, obj + 0x10, 8)
        if not d:
            return None
        bi = struct.unpack("<Q", d)[0]
        if not (0x10000 < bi < 0x7FFFFFFFFFFF):
            return None
        cid = read(h, bi + 0x10, 4)
        if cid and len(cid) == 4:
            v = struct.unpack("<i", cid)[0]
            if 10000000 <= v <= 99999999:
                return v
    except Exception:
        pass
    return None


def read_special_cards(h, player_obj):
    """
    读玩家的 **SpecialCardList**（+0x78）：被认领的光环存在这里
    （ClaimAuraDataAction 会 new 一个 BattleCardBase、用 SpecialCardId 填进去）。
    返回卡号列表。
    """
    out = []
    try:
        d = read(h, player_obj + 0x78, 8)
        if not d:
            return out
        ptr = struct.unpack("<Q", d)[0]
        items = read_list_objects(h, ptr, maxn=64)
        for addr in (items or []):
            if not isinstance(addr, int):
                continue
            cid = read_card_id(h, addr)
            if cid:
                out.append(cid)
                continue
            # 光环特殊卡是 new 出来的 BattleCardBase，没有 _buildInfo → CardId 读不到。
            # 但 SetCardId 会设置 BaseParameter（指向该卡号参数对象），
            # 所以顺着它的指针找 6xxxxxxx / 1xxxxxxx 的卡号。
            try:
                raw = read(h, addr + 0x10, 0x60)
                if not raw:
                    continue
                found = None
                for i in range(0, len(raw) - 7, 8):
                    q = struct.unpack_from("<Q", raw, i)[0]
                    if not (0x10000 < q < 0x7FFFFFFFFFFF):
                        continue
                    blob = read(h, q, 0x80)
                    if not blob:
                        continue
                    for k in range(0, len(blob) - 3, 4):
                        v = struct.unpack_from("<i", blob, k)[0]
                        if 60000000 <= v <= 60100000 or 10000000 <= v <= 10999999:
                            found = v
                            break
                    if found:
                        break
                if found:
                    out.append(found)
            except Exception:
                pass
    except Exception:
        pass
    return out

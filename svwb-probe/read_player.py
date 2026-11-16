#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
读取「幻境战斗中双方的幻境等级 + 场上随从」—— 目标 Sv2WarchessBattlePlayerBase。

为什么换这个类（而不是 S_COMBAT_OPPONENT）：
  · S_COMBAT_OPPONENT 是**战斗开场的瞬时 DTO**，回主界面就被 GC，极难掐时机
  · Sv2WarchessBattlePlayerBase 在整场战斗中**恒有 2 个实例**（我方/对方），随时可读

类字段（来自 dnmeta 从 sv2Warchess.bytes 解出的元数据，23 字段）：
    long <UserId>              int <HeroId>      int <Life>       int <Armor>
    int  <Grade>               int <EvolvePoint> int <EvolveUnlocked>
    int  <SuperEvolveUnlocked> int <MaxEvolvePoint> int <SuperEvolvePoint>
    int  <MaxSuperEvolvePoint> bool <IsPriviliged> bool <IsShowHighBorder>
    int  <ShowBorderInfo>      int <DoubleSkillId>  int <HandCardNum>
    List<BattleCardBase> <ShopCardList> / <HandCards> / <BattleCards> ...

★ 核心技巧：**不猜字段偏移**。
  HeroId / Life / Armor / Grade 是**连续 4 个 int**，数值范围极有辨识度：
      HeroId 1~300、Life 0~40、Armor 0~30、Grade 1~8
  所以在实例的 0x10~0x120 之间按 4 字节步进找"满足这个四元组"的位置，
  命中位置就是 Life 的真实偏移 → 其余字段按相对位置推算。
  这样无论 HybridCLR 用哪种布局都能自适应。

随从读取链：
  BattlePlayerBase.<BattleCards> (List<T>)
    → List._items(数组指针) / List._size(长度)
    → BattleCardBase：Star / Atk / Life / uuid
    → BattleCardBase.<BaseParameter> → Sv2WarchessCardParameter.<CardId> → 卡名

用法：
  python read_player.py                 # 读一次
  python read_player.py --watch 2       # 每 2 秒刷新（战斗中盯着看）
  python read_player.py --probe         # 只打印各类命中数的时间序列
  python read_player.py --dump 0x1234   # dump 某实例的字段区（人工核对布局）

全程**只读**（OpenProcess + ReadProcessMemory），不写入、不注入、不 hook。
"""
import argparse
import json
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 复用已有的底层读写与类定位
from shadow_warchess_reader import (  # noqa: E402
    open_proc, find_game_pid, find_class, read, rd, scan_qwords,
    cstr, read_array, load_cards, card_name,
)

CARDS_JSON = os.path.join(HERE, "..", "dsh-shadowverse-huanjing",
                          "lib", "knowledge", "cards.json")

# 各类的"数值指纹"约束（用于自适应定位字段偏移）
HERO_ID = (1000, 10000)   # 实测英雄 ID 都是 1000+；出现过 hero=1 的假候选，所以下限提到 1000    # 实测英雄 ID 是 1005 这种四位数，400 太小会把真值漏掉
LIFE = (0, 40)
ARMOR = (0, 40)
GRADE = (1, 8)
EVOLVE = (0, 12)

HEADER = 0x10          # 对象头：klass@0 + monitor@8


PID_HOLDER = {"pid": None}

CACHE_PATH = os.path.join(HERE, "logs", "_class_cache.json")


def _load_cache():
    try:
        with open(CACHE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_cache(d):
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
    except Exception:
        pass


def get_class_fast(h, tname, nsname, pid):
    """
    取 Il2CppClass 地址，**优先用缓存**。

    为什么需要：find_class 每次要做两遍全内存扫描（约 5.4GB x 2 ≈ 20 秒），
    做实时仪表盘完全不可接受。类地址在**同一进程生命周期内固定不变**，
    所以缓存起来，下次只需读几个字节验证即可。
    """
    cache = _load_cache()
    ent = (cache.get(str(pid)) or {}).get(tname)
    if ent and ent.get("class"):
        try:
            cb = int(ent["class"], 16)
        except Exception:
            cb = 0
        if cb:
            nm = read(h, cb + 0x10, 8)
            if nm:
                p = struct.unpack("<Q", nm)[0]
                if cstr(h, p) == tname:
                    return cb, True
    r = find_class(h, tname, nsname)
    cb = r[0] if isinstance(r, tuple) else r
    if cb:
        cache.setdefault(str(pid), {})[tname] = {"class": hex(cb)}
        _save_cache(cache)
    return cb, False


def screen_phase(img):
    """
    判定当前是「运营阶段」还是「战斗阶段」。

    用户明确要求：**运营阶段不需要读数据算胜率**（那时场上摆的是下回合的阵容，
    跟本回合战斗无关）。所以只在战斗阶段读。

    复用 harvest.py 里已经调好的判别（它给抓帧打的就是"运营/战斗"标签）：
      1) 左侧 8 人顺序栏的红血条比例 >= 0.025 → 在对局内
      2) 兔子（宠物）模板分 >= 阈值 → 运营阶段；否则战斗阶段
    返回 'battle' | 'operate' | 'other'
    """
    try:
        import sys as _s
        _tools = os.path.join(HERE, "..", "dsh-shadowverse-huanjing", "tools")
        if _tools not in _s.path:
            _s.path.insert(0, _tools)
        import harvest as HV
        return HV.phase_of(img)
    except Exception:
        return "unknown"


def find_instances(h, tname, nsname, pid=None):
    """定位类 → 扫描所有疑似实例地址"""
    # ⚠️ find_class 在 shadow_warchess_reader 里只返回**地址**（不是元组），
    #    这里做兼容处理，避免 unpack 报错。
    if pid:
        cb, _cached = get_class_fast(h, tname, nsname, pid)
        nhit = -1
    else:
        r = find_class(h, tname, nsname)
        cb = r[0] if isinstance(r, tuple) else r
        nhit = r[1] if isinstance(r, tuple) else -1
    if not cb:
        return None, [], nhit
    sites = scan_qwords(h, {cb})
    # 只要形象合理的（非类表引用）：对象头后第一个字段不能是空
    good = []
    for a in sites:
        head = read(h, a, 16)
        if not head or len(head) < 16:
            continue
        klass = struct.unpack_from("<Q", head, 0)[0]
        if klass != cb:
            continue
        # 排除"类型表引用"：那种位置的 +0x08 往往也是 class 指针
        mon = struct.unpack_from("<Q", head, 8)[0]
        if mon == cb:
            continue
        good.append(a)
    return cb, good, nhit


def probe_field_offsets(h, addr, span=0x140):
    """
    在实例的 [HEADER, HEADER+span) 里按 4 字节步进，找
    HeroId/Life/Armor/Grade 连续 4 个 int 的位置。

    返回 [(offset, hero, life, armor, grade, evolve_point), ...] 按可信度排序
    """
    raw = read(h, addr + HEADER, span)
    if not raw or len(raw) < 32:
        return []
    n = len(raw) // 4
    vals = list(struct.unpack("<%di" % n, raw[:n * 4]))
    out = []
    for i in range(n - 4):
        hero, life, armor, grade = vals[i], vals[i + 1], vals[i + 2], vals[i + 3]
        if not (HERO_ID[0] <= hero <= HERO_ID[1]):
            continue
        if not (LIFE[0] <= life <= LIFE[1]):
            continue
        if not (ARMOR[0] <= armor <= ARMOR[1]):
            continue
        if not (GRADE[0] <= grade <= GRADE[1]):
            continue
        evo = vals[i + 4] if i + 4 < n else -1
        # ★ 关键判别：MaxEvolvePoint 在 EvolvePoint 之后 +0x0C 处，
        #   且必有 0 <= EvolvePoint <= MaxEvolvePoint。
        #   没有这条时，错位的 (Life,Armor,Grade,EvolvePoint) 四元组也会命中
        #   （实测 35/2/4/3 与 11/35/2/4 都能通过宽泛的范围检查）。
        # ★ 最强判别：HeroId 前面 8 字节是 UserId（long）。
        #   玩家 ID 是 9~13 位的十进制数，这是"布局无关"的硬特征，
        #   比 MaxEvolvePoint 可靠得多（实测 maxev 有时是 0，会把真的漏掉）。
        #   注意 start 是 HEADER+i*4，hero 之前的 8 字节 = vals[i-2]。
        if i < 2:
            continue
        uid = vals[i - 2] | (vals[i - 1] << 32)
        if uid < 0:
            uid += 1 << 64
        if not (10 ** 8 <= uid <= 10 ** 14):
            continue
        maxevo = vals[i + 7] if i + 7 < n else 0
        score = 10
        if 3 <= maxevo <= 12:
            score += 2
            if evo > maxevo:
                continue
        # ⚠️ 不给"血量/护甲小"加分：错位四元组血量往往很小，反而会得高分。
        out.append((HEADER + i * 4, hero, life, armor, grade, evo, score, uid))
    out.sort(key=lambda t: -t[6])
    return out


def read_list_objects(h, list_ptr, maxn=16):
    """
    读 System.Collections.Generic.List<T>：
      +0x10 _items (T[] 指针)
      +0x18 _size  (int)   —— .NET 4.x/5+ 常见
      +0x20 _size          —— 少数变体
    返回对象指针列表
    """
    if not list_ptr or list_ptr < 0x10000:
        return []
    head = read(h, list_ptr, 0x28)
    if not head or len(head) < 0x20:
        return []
    items = struct.unpack_from("<Q", head, 0x10)[0]
    size18 = struct.unpack_from("<i", head, 0x18)[0]
    size20 = struct.unpack_from("<i", head, 0x20)[0] if len(head) >= 0x28 else -1
    size = size18 if 0 <= size18 <= 64 else (size20 if 0 <= size20 <= 64 else None)
    if size is None:
        return []
    # 注意：read_array 返回的是**对象指针列表**（整数），不是 (ptr, obj) 元组
    objs = read_array(h, items, limit=min(size, maxn))
    return [o for o in objs if o and o > 0x10000][:size]


def find_pointer_field(h, addr, start, span, want_obj):
    """
    在 [start, start+span) 里按 8 字节步进找指针，用回调 want_obj(ptr) 判断是不是目标。
    返回第一个通过的偏移。
    """
    raw = read(h, addr + start, span)
    if not raw:
        return None
    for off in range(0, len(raw) - 8 + 1, 8):
        p = struct.unpack_from("<Q", raw, off)[0]
        if p < 0x10000 or p > 0x7FFFFFFFFFFF:
            continue
        if want_obj(p):
            return start + off
    return None


def looks_like_card_list(h, ptr):
    """判断 ptr 是否指向一个"装着 BattleCardBase"的 List"""
    objs = read_list_objects(h, ptr, maxn=8)
    if not objs:
        return False
    # 至少要有一个元素，且元素的 Star/Atk/Life 合理
    for o in objs[:4]:
        raw = read(h, o + HEADER, 0x60)
        if not raw or len(raw) < 0x40:
            continue
        vals = struct.unpack("<%di" % (len(raw) // 4), raw[:len(raw) // 4 * 4])
        # 找一组连续 int：Star 1..7, Atk 0..99, Life 0..99
        for i in range(len(vals) - 3):
            st, atk, lf = vals[i], vals[i + 1], vals[i + 2]
            if 1 <= st <= 7 and 0 <= atk <= 99 and 0 <= lf <= 99:
                return True
    return False


def read_card(h, obj):
    """读一个 BattleCardBase：Star / Atk / Life / card_id"""
    raw = read(h, obj + HEADER, 0x100)
    if not raw or len(raw) < 0x40:
        return None
    n = len(raw) // 4
    vals = struct.unpack("<%di" % n, raw[:n * 4])
    best = None
    for i in range(n - 3):
        st, atk, lf = vals[i], vals[i + 1], vals[i + 2]
        if 1 <= st <= 7 and 0 <= atk <= 99 and 0 <= lf <= 99:
            score = 3
            if atk <= 60:
                score += 1
            if lf <= 60:
                score += 1
            if best is None or score > best[0]:
                best = (score, HEADER + i * 4, st, atk, lf)
    if not best:
        return None
    _s, star_off, st, atk, lf = best
    card = {"obj": hex(obj), "star": st, "atk": atk, "life": lf,
            "card_id": None, "name": None}

    # 试着从 BaseParameter → CardId 拿卡 ID
    for off in range(HEADER, HEADER + 0x40, 8):
        p = struct.unpack_from("<Q", raw, off - HEADER)[0] if off - HEADER + 8 <= len(raw) else 0
        if p < 0x10000 or p > 0x7FFFFFFFFFFF:
            continue
        prm = read(h, p + HEADER, 0x40)
        if not prm or len(prm) < 0x10:
            continue
        pv = struct.unpack("<%di" % (len(prm) // 4), prm[:len(prm) // 4 * 4])
        for j, v in enumerate(pv):
            if 1000000 <= v <= 99999999:      # 卡 ID 通常是 8 位数
                card["card_id"] = v
                card["param_off"] = hex(HEADER + j * 4)
                break
        if card["card_id"]:
            break
    return card


def read_our_grade(h, pid):
    """
    读**我方**幻境等级：Sv2Warchess.TavernOwnerData.<CurGrade>。

    为什么用它：两个 BattlePlayerBase 实例的 uid 可能相同（同一玩家的多份引用），
    光看实例分不出敌我；而 TavernOwnerData 是**本地玩家**的酒馆数据，
    它的 CurGrade 一定是我方等级，可以拿来对照。
    """
    cb, _cached = get_class_fast(h, "TavernOwnerData", "Sv2Warchess", pid)
    if not cb:
        return None
    sites = scan_qwords(h, {cb})
    best = None
    for a in sites:
        raw = read(h, a + HEADER, 0x20)
        if not raw or len(raw) < 0x10:
            continue
        # CurGrade 是 short，紧跟在 vtable/monitor 后面；按 2/4 字节都试，
        # 取 1..8 且后面跟着合理 gold 的那个。
        for off in range(0, 0x10, 2):
            v = struct.unpack_from("<h", raw, off)[0]
            if 1 <= v <= 8:
                # 相邻的 UpgradeCost/RefreshCost 也应是小的正数
                nxt = struct.unpack_from("<h", raw, off + 2)[0] if off + 4 <= len(raw) else -1
                score = 3 if 0 <= nxt <= 20 else 1
                if best is None or score > best[0]:
                    best = (score, v, a, off)
    return best[1] if best else None


def snapshot_player(h, cards, own_hp=None):
    cb, insts, nhit = find_instances(h, "Sv2WarchessBattlePlayerBase", "Sv2Warchess", pid=PID_HOLDER.get("pid"))
    out = {"class": hex(cb) if cb else None, "name_hits": nhit,
           "instances": len(insts), "players": []}
    if not cb:
        out["error"] = "找不到 Sv2WarchessBattlePlayerBase 的 Il2CppClass"
        return out
    if not insts:
        out["error"] = "找到类但没有实例（可能不在战斗阶段）"
        return out

    # ⚠️ 必须遍历**全部**实例：真实实例在列表里可能排到第 100 位之后，
    #    之前只取前 8 个，导致明明能找到的值被漏掉。
    for a in insts:
        cands = probe_field_offsets(h, a)
        if not cands:
            continue
        off, hero, life, armor, grade, evo, score, uid = cands[0]
        p = {"instance": hex(a), "field_off": hex(off), "score": score,
             "hero_id": hero, "life": life, "armor": armor,
             "grade": grade, "evolve_point": evo, "cards": []}
        # 找所有"像卡牌列表"的指针字段。
        # ⚠️ 之前只在 off+0x10 起 0x60 范围内找**第一个**匹配，
        #    对方那侧就报"未找到列表指针"——因为对象里有 6 个 List 字段
        #    （ShopCardList / HandCards / BattleCards / HeroSkillCardList /
        #     SkillCards / SpecialCardList），第一个不一定是 BattleCards。
        #    现在全扫 0x10~0x130，把每个有效列表都记下来，
        #    再按"元素能解出合法卡牌"的数量挑最可信的那个。
        lists = []
        for lo in range(HEADER, 0x130, 8):
            rawp = read(h, a + lo, 8)
            if not rawp:
                continue
            ptr = struct.unpack("<Q", rawp)[0]
            if ptr < 0x10000 or ptr > 0x7FFFFFFFFFFF:
                continue
            objs = read_list_objects(h, ptr, maxn=12)
            if not objs:
                continue
            parsed = []
            for o in objs:
                c = read_card(h, o)
                if c:
                    c["name"] = card_name(cards, c["card_id"]) if c.get("card_id") else None
                    parsed.append(c)
            if parsed:
                lists.append((len(parsed), lo, parsed))
        if lists:
            # 元素最多、且带 card_id 的那个最可能是场上随从列表
            lists.sort(key=lambda t: (-sum(1 for c in t[2] if c.get("card_id")), -t[0]))
            n_best, lo_best, parsed = lists[0]
            p["battle_cards_off"] = hex(lo_best)
            p["cards"] = parsed
            p["list_candidates"] = [{"off": hex(l), "n": n} for n, l, _ in lists]
        out["players"].append(p)
    out["players"].sort(key=lambda d: -d["score"])
    # ⚠️ **先按可信度截断，再谈敌我**。
    #    之前是"谁的血量对上我方就选谁"，结果一个垃圾候选
    #    （hero=1, grade=1）因为 life 恰好等于我方血量被选中，
    #    读出了完全错误的面板 —— 假值比没值更糟。
    #    正确做法：只保留得分最高的那一档（同分视为并列），敌我只在其中判。
    if out["players"]:
        top = out["players"][0]["score"]
        out["players"] = [p for p in out["players"] if p["score"] >= top - 1]
        out["candidates_kept"] = len(out["players"])
    # ★ 判别敌我。
    #   1) 先按 **uid 去重**：同一玩家的对象在内存里常有多份引用，
    #      同一个 uid 出现两次只是重复，不代表"我方+对方"。
    #   2) 再用 TavernOwnerData.<CurGrade>（本地玩家等级）认我方。
    by_uid = {}
    for p in out["players"]:
        k = p.get("uid")
        if k is None:
            continue
        if k not in by_uid or (p.get("score") or 0) > (by_uid[k].get("score") or 0):
            by_uid[k] = p
    if by_uid:
        out["distinct_uids"] = list(by_uid.keys())
        out["players"] = list(by_uid.values())

    pid = PID_HOLDER.get("pid")
    our_grade = read_our_grade(h, pid) if pid else None
    out["our_grade"] = our_grade

    for p in out["players"]:
        p.pop("is_me", None)
    if our_grade is not None:
        cand = [p for p in out["players"] if p.get("grade") == our_grade]
        if len(cand) == 1:
            cand[0]["is_me"] = True
            for p in out["players"]:
                if p is not cand[0]:
                    p["is_me"] = False
    if own_hp is not None and not any(p.get("is_me") is True for p in out["players"]):
        # 兜底：用截图血量对
        cand = [p for p in out["players"] if p.get("life") == own_hp]
        if len(cand) == 1:
            cand[0]["is_me"] = True
            for p in out["players"]:
                if p is not cand[0]:
                    p["is_me"] = False
    out["own_hp"] = own_hp
    return out


def dump_instance(h, addr, span=0x140):
    print(f"=== dump 实例 {addr} 的 [{HEADER:#x}, {HEADER+span:#x}) ===")
    raw = read(h, addr + HEADER, span)
    if not raw:
        print("读取失败")
        return
    for off in range(0, len(raw) - 8 + 1, 8):
        q = struct.unpack_from("<Q", raw, off)[0]
        i0, i1 = struct.unpack_from("<ii", raw, off)
        print(f"  +{HEADER+off:#04x}  q={q:#018x}  i32=[{i0:>12}, {i1:>12}]  u16={q & 0xffff:>6}")


def render(res):
    if "error" in res:
        print(f"[!] {res['error']}")
        print(f"    class={res.get('class')} name_hits={res.get('name_hits')} "
              f"instances={res.get('instances')}")
        return
    print(f"类实例 {res['instances']} 个（战斗中应有 2 个：我方 + 对方）")
    own = res.get("own_hp")
    for i, p in enumerate(res["players"]):
        if p.get("is_me") is True:
            tag = "我方"
        elif p.get("is_me") is False:
            tag = "对方"
        else:
            tag = f"#{i}（敌我未定）"
        print(f"  [{tag}] {p['instance']}  字段偏移={p['field_off']} 分={p['score']}")
        print(f"       英雄={p['hero_id']}  血量={p['life']}  护甲={p['armor']}  "
              f"★幻境等级={p['grade']}  进化点={p['evolve_point']}")
        if p.get("battle_cards_off"):
            print(f"       场上随从 {len(p['cards'])} 张（列表偏移 {p['battle_cards_off']}）:")
            for c in p["cards"]:
                nm = c.get("name") or "?"
                cid = c.get("card_id")
                print(f"         {c['star']}★ {c['atk']}/{c['life']}  "
                      f"card_id={cid}  {nm}")
        else:
            print("       场上随从：未找到列表指针")
        if p.get("list_candidates"):
            print("       列表候选: " + ", ".join(
                f"{d['off']}×{d['n']}" for d in p["list_candidates"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--watch", type=float, default=0)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--dump")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pid = args.pid or find_game_pid()[0]
    if not pid:
        print("找不到游戏进程")
        return 1
    h = open_proc(pid)
    PID_HOLDER["pid"] = pid
    cards = load_cards()
    print(f"# pid={pid} 卡片 {len(cards)} 张（只读，无写入/注入）\n", file=sys.stderr)

    if args.dump:
        dump_instance(h, int(args.dump, 0))
        return 0

    while True:
        t0 = time.time()
        if args.probe:
            cb, n = find_class(h, "Sv2WarchessBattlePlayerBase", "Sv2Warchess")
            insts = scan_qwords(h, {cb}) if cb else []
            print(f"{time.strftime('%H:%M:%S')}  类={hex(cb) if cb else None}  "
                  f"疑似实例={len(insts)}")
        else:
            try:
                # ★ 先判阶段：运营阶段不读（用户要求）
                import read_own_hp as _ROH
                _img = _ROH.grab_screen()
                ph = screen_phase(_img) if _img is not None else "unknown"
                if ph == "operate":
                    print("【运营阶段】跳过（此时场上不是本回合的战斗阵容）")
                    if not args.watch:
                        break
                    time.sleep(args.watch or 1)
                    continue
                if ph == "other":
                    print("【不在对局】跳过")
                    if not args.watch:
                        break
                    time.sleep(args.watch or 1)
                    continue
                # 战斗阶段：读画面上我方血量，用于判别哪个内存实例是我方
                own_hp = None
                try:
                    if _img is not None:
                        own_hp, _pr = _ROH.read_hp(_img)
                except Exception:
                    own_hp = None
                res = snapshot_player(h, cards, own_hp=own_hp)
            except Exception as e:
                res = {"error": f"{type(e).__name__}: {e}"}
            if args.json:
                print(json.dumps(res, ensure_ascii=False, indent=1))
            else:
                render(res)
        print(f"   [{time.time()-t0:.1f}s]", file=sys.stderr)
        if not args.watch:
            break
        time.sleep(args.watch)
    return 0


if __name__ == "__main__":
    sys.exit(main())

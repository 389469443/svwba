#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗 · 记牌器窗口（2026-10-21）

Firestone 式：列出你的卡组，标出还没抽到的牌。
数据来自 `deck_reader.snapshot()`；未校准/未对战时显示「未在对战中」。
只读；不读对手手牌/牌库。
"""
import os
import sys
import time
import threading
import io
import json

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import tkinter as tk

BG = "#12161d"
BG2 = "#171c25"
FG = "#efe9dc"
GOLD = "#c9a86a"
GOLD2 = "#8d7a52"
DIM = "#9aa7a0"
RULE = "#2a3038"
RED = "#c07b6b"
GREEN = "#6fbf73"

POLL_SEC = 0.5

# 「推定结果」的改判窗口：认输 / 掉线这种双方都活着就结束的对局，探针判不出
# 谁投的降，按血量猜（血少的一方认输，见 _guess_by_life），同时在这段时间内
# 顶栏留一个「改判」按钮（用户 2026-11-06：我方主动认输没统计失败）。
FLIP_WINDOW = 120.0
# gone 事件至少要「连续读不到我方」这么久才允许推定认输 —— 长考也会读不到，
# 时间太短容易把思考记成投降。
GONE_GUESS_MS = 20000

# 衍生物（token）卡的 id 前缀。卡表 5910 张里，90 / 95 开头的各 113 种互为镜像，
# 全是「只能由卡牌能力生成、不可能出现在卡组里」的卡（解析的创造物、一尾狐、
# 乙姬近卫队……）；1x / 7x 开头才是能组进牌库的正常卡，8x 是占位卡。
TOKEN_PREFIX = ("90", "95")


def is_token_card(cid):
    """这张卡是不是衍生物（token）。"""
    try:
        return str(int(cid)).zfill(8)[:2] in TOKEN_PREFIX
    except Exception:
        return False


def _live_mtime():
    """常驻读取最后一次写 deck_live.json 的时间（用来判断状态是不是新的）。"""
    try:
        import deck_reader
        return os.path.getmtime(deck_reader.LIVE_PATH)
    except Exception:
        return 0.0


try:
    import cardart
except Exception:
    cardart = None

# 「获得 · XX」推测表（从卡表技能原文自动生成）。缺文件/解析失败都不能
# 影响记牌器本体，所以整体包一层 try。
try:
    import foe_grants
except Exception:
    foe_grants = None


def on_play(cid):
    if foe_grants is None:
        return []
    try:
        return foe_grants.on_play(cid)
    except Exception:
        return []


FOE_TAG = (foe_grants.TAG if foe_grants else "推测")


def on_evolve(cid, ev=2, prev=0):
    """这张卡「进化/超进化」时给的牌。

    ev=1 → 「进化时」；ev>=2 → 「进化时」+「超进化时」（超进化会同时触发两者，
    用户 2026-11-17 确认）。prev 是这张牌之前记过的状态，用于 1→2 升级时只补差值。
    """
    if foe_grants is None:
        return []
    try:
        return foe_grants.on_evolve(cid, ev, prev)
    except TypeError:
        # 老版本 foe_grants 签名不同：按老语义退回去
        try:
            return foe_grants.on_evolve(cid)
        except Exception:
            return []
    except Exception:
        return []


def on_death(cid):
    if foe_grants is None:
        return []
    try:
        return foe_grants.on_death(cid)
    except Exception:
        return []


_ART_CACHE = [0.0, True]


def art_on():
    """要不要显示卡图（logs/hud_options.json 里的 art，控制台那个勾选框写它）。

    读文件很轻，但每 0.5 秒读一次没必要，缓存 2 秒。
    """
    now = time.time()
    if now - _ART_CACHE[0] < 2.0:
        return _ART_CACHE[1]
    on = bool(cardart.enabled()) if cardart else False
    _ART_CACHE[0], _ART_CACHE[1] = now, on
    return on


_OPEN = [None]

# 手牌分区：游戏里手牌本来就在屏幕上，但记牌器把它们按费用/类型排好、
# 还标出「现在能不能打出」，对照着看比在屏幕上数更快。
# 2026-11-06 曾因「重复信息」关掉，2026-11-08 用户要求恢复（V1.0）。
# 分区挂在牌库上面、并且可以点标题折叠，嫌占地方收起来即可。
SHOW_HAND = True

# 记牌器窗口尺寸（用户拖右下角改过之后记下来，下次照原样开）
SIZE_FILE = os.path.join(HERE, "logs", "deck_win.json")


def _load_win_size():
    try:
        with io.open(SIZE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        w = int(d.get("w") or 360)
        h = int(d.get("h") or 0)
        return max(300, w), (max(260, h) if h else 0)
    except Exception:
        return 360, 0


def _save_win_size(w, h):
    try:
        d = os.path.dirname(SIZE_FILE)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with io.open(SIZE_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps({"w": int(w), "h": int(h or 0)}))
    except Exception:
        pass


class _MiniBar(tk.Canvas):
    """自绘的细滚动条。

    系统自带的 tk.Scrollbar 在 Windows 上走原生主题，配色参数全被忽略，
    在深色 HUD 上会露出一条白杠，很扎眼。这里用一个 5px 的 Canvas 画滑块，
    只在内容溢出时出现。
    """

    def __init__(self, master, target, width=5, **kw):
        tk.Canvas.__init__(self, master, width=width, bg=BG, bd=0,
                           highlightthickness=0, takefocus=0, **kw)
        self.target = target
        self._first, self._last = 0.0, 1.0
        self._barw = width   # ⚠️ 不能叫 _w：tkinter 用 _w 存控件路径
        self.thumb = self.create_rectangle(1, 0, width - 1, 0,
                                           fill="#3d4650", outline="")
        self.bind("<Configure>", lambda _e: self._sync())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag_to)
        target.configure(yscrollcommand=self._on_view)

    # --- canvas 滚动回调 ---
    def _on_view(self, first, last):
        try:
            self._first, self._last = float(first), float(last)
        except Exception:
            return
        self._sync()

    def _sync(self):
        h = self.winfo_height()
        if h < 12:
            return
        if self._first <= 0.001 and self._last >= 0.999:
            self.itemconfigure(self.thumb, state="hidden")
            return
        self.itemconfigure(self.thumb, state="normal")
        top = int(self._first * h)
        bot = int(self._last * h)
        if bot - top < 16:
            bot = top + 16
        if bot > h:
            bot, top = h, max(0, h - 16)
        self.coords(self.thumb, 1, top, self._barw - 1, bot)

    # --- 拖动 / 点击 ---
    def _press(self, event):
        self._drag_to(event)

    def _drag_to(self, event):
        h = max(1, self.winfo_height())
        try:
            self.target.yview_moveto(max(0.0, min(1.0, event.y / float(h))))
        except Exception:
            pass



def _font(size, bold=False):
    import tkinter.font as tkfont
    return tkfont.Font(family="Microsoft YaHei UI", size=size,
                       weight="bold" if bold else "normal")


def _card_name(cid):
    """卡名查询：先查影之诗卡表（8 位 id），再退回幻境卡库，最后 #id。"""
    try:
        import deck_reader
        v = deck_reader.card_name(cid)
        if v:
            return v
    except Exception:
        pass
    try:
        import meta_stats
        names = meta_stats.load_cards_db()
        return names.get(int(cid)) or ("#%d" % cid)
    except Exception:
        return "#%d" % cid


def _ev_map(cards):
    """战场随从 → {实例键: (卡 id, 进化状态)}，状态 0 未进化 / 1 进化 / 2 超进化。

    2026-11-17：卡对象上直接有 <EvolveState>，不用再靠身材猜。
    键优先用 <UniqueId>（同名的两张牌也能分开），老快照没有就退回卡 id。
    """
    out = {}
    for c in cards or []:
        if not isinstance(c, dict):
            continue
        try:
            cid = int(c.get("cardId") or 0)
        except Exception:
            continue
        if not cid:
            continue
        key = c.get("uid") or ("c%d" % cid)
        ev = int(c.get("ev") or 0)
        if key not in out or ev > out[key][1]:
            out[key] = (cid, ev)
    return out


class DeckTracker:
    def __init__(self, master=None):
        self.root = tk.Toplevel(master) if master is not None else tk.Tk()
        # 窗口是无边框的，标题看不见；版本号画在头部（见 deck_hud._paint_head）。
        # 这里仍带上版本，任务管理器 / 窗口工具里能认出是哪一版。
        try:
            from app_ver import VERSION as _V
        except Exception:
            _V = ""
        self.root.title("影之诗 · 记牌器" + (" " + _V if _V else ""))
        self.root.overrideredirect(True)
        try:
            self.root.attributes("-alpha", 0.96)
        except Exception:
            pass
        self.root.configure(bg=BG)
        _w0, _h0 = _load_win_size()
        self.root.geometry("%dx%d" % (_w0, _h0 or 460))
        self.root.resizable(True, True)
        self._drag = None
        self._fitting = False
        self._pending_fit = None
        self.f_t = _font(12, True)
        self.f_s = _font(11)
        self._after = None
        self._last = None
        self._snapshot_busy = False
        self._pending_snapshot = None
        self._snapshot_lock = threading.Lock()
        # 对局结束事件（frida_live.py 写 logs/battle_end.json）
        self._last_end_at = 0          # 已处理过的最后一个结束事件的毫秒时间戳
        self._opened_at = time.time()  # 本次窗口启动时间：更早的事件不补记

        import deck_hud
        self.hud = deck_hud.DeckHud(
            self.root, width=_w0, height=_h0,
            on_close=self.close,
            on_ai=self._open_ai,
            on_reset=self._reset_winrate,
            on_flip=self._flip_last,
            on_resize=_save_win_size,
            art_get=(cardart.get if cardart else None),
            art_has=(cardart.has if cardart else None),
            max_body=max(360, min(620, int(self.root.winfo_screenheight()) - 280)))
        self._stats = {"win": 0, "loss": 0, "rate_txt": "0%"}
        self.companion = None           # 陪玩实例（on_ai 里按需创建）
        self._bubble = (None, 0.0)      # 陪玩说话 → 顶栏右侧短暂显示
        self._foe = {"addr": None, "played": {}, "token": {}, "n": 0}
        self._pp_obs = {"last": None, "n": 0}   # 内存 PP 是否「活着」的判据
        self._refresh_winrate()
        # 对局结束检测：记录胜负用
        self._was_in_battle = False
        self._last_life = None
        self._min_life = None
        self._last_enemy_life = None
        self._battle_started = 0
        # 一局只记一次：按探针发来的「本局流水号」去重。比「间隔 N 秒只记一次」
        # 可靠得多 —— 同一局里探针就算多发了结束事件，seq 也一样，全被挡掉。
        self._recorded_seq = None
        self._last_seq = None
        self._last_record_at = 0
        self._pending_end = None        # 挂起中的 gone 事件（等确认）
        self._idle_since = 0.0          # 从什么时候开始读到「不在对局中」
        # 推定结果（认输 / 掉线）：记着是哪一局推定的，好在对局其实没结束时撤销
        self._guess_seq = None
        self._guess_at = 0.0
        self._guess_d = {}              # 那一条结束事件（判断「其实还是同一局」用）
        self._guess_undone = None       # 已经撤过一次的 seq（不反复撤）
        self._guess_win = None          # 推定的是胜还是负（撤销时日志别写反）

        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self.poll()

    # ---------------- 拖动 ----------------
    def _drag_start(self, ev):
        self._drag = (ev.x_root - self.root.winfo_x(),
                      ev.y_root - self.root.winfo_y())

    def _drag_move(self, ev):
        if not self._drag:
            return
        self.root.geometry("+%d+%d" % (ev.x_root - self._drag[0],
                                       ev.y_root - self._drag[1]))

    # ---------------- 轮询 ----------------
    def poll(self):
        self._after = None
        # 胜率可能被别处改了：控制台「历史胜率」在切卡组 / 跨天时会把
        # battle_result.json 归档后整个删掉。盯一下时间戳，变了就重读，
        # 否则顶栏会一直挂着清零前的旧数字。
        now0 = time.time()
        if now0 - getattr(self, "_stats_chk", 0) >= 2.0:
            self._stats_chk = now0
            try:
                import battle_stats as _bs
                mt = (os.path.getmtime(_bs.PATH)
                      if os.path.exists(_bs.PATH) else 0)
                if mt != getattr(self, "_stats_mtime", None):
                    self._stats_mtime = mt
                    if getattr(self, "_stats_seen", False):
                        self._refresh_winrate()
                    self._stats_seen = True
            except Exception:
                pass
        snap = self._pending_snapshot
        self._pending_snapshot = None
        if snap is None:
            with self._snapshot_lock:
                if not self._snapshot_busy:
                    self._snapshot_busy = True
                    threading.Thread(target=self._read_snapshot_worker,
                                     daemon=True).start()
            try:
                self._after = self.root.after(int(POLL_SEC * 1000), self.poll)
            except Exception:
                pass
            return
        # 对局结束检测：
        # - 先看 frida 写下的「结束事件」（双方最后血量），这是最准的一条路；
        # - gone 事件不立即记账，先挂几秒确认对局真的没继续；
        # - 同一局只记一次：按探针的 seq 去重（同一局多发了也不怕）。
        self._check_battle_end()
        try:
            in_battle = bool(snap.get("in_battle"))
            holding = bool(snap.get("hold"))
            now = time.time()
            self._settle_pending_end(in_battle, now, holding)
            if in_battle and not holding:
                # 推定「认输」之后对局又活了（长考）→ 撤销那条负
                self._maybe_undo_guess(snap)
            if in_battle:
                self._on_battle_frame(snap, now)
            elif getattr(self, "_was_in_battle", False):
                # 读到「不在对局中」也要缓 1.5 秒：探针偶尔会有一帧读不到我方，
                # 以前这里立刻把「对手已知牌 / PP 记忆」清空，用户看到的就是
                # 对手那一栏突然空掉、PP 从 0 重新数。
                if not self._idle_since:
                    self._idle_since = now
                elif now - self._idle_since >= 1.5:
                    self._was_in_battle = False
                    self._battle_started = 0
                    self._last_life = None
                    self._min_life = None
                    self._last_enemy_life = None
                    self._idle_since = 0.0
                    # 这里**不要**整个重建 self._foe。
                    #   探针会短暂报「不在对局中」（对手长考、结算画面、hard 误报），
                    #   一重建就把「对手已知」连同「已知衍生物」整栏清空 ——
                    #   用户 2026-11-11：「对方同名随从进战场时那一栏被清掉」。
                    #   真正该清的东西（played / token / grant / dead / seen）
                    #   已经由 _track_foe 的「流水号变了 = 换局」统一处理，
                    #   那个判据只在真开新局时成立，比这里的 1.5 秒可靠得多。
                    self._foe["addr"] = None
                    self._pp_obs = {"last": None, "n": 0}
        except Exception:
            pass
        try:
            self.render(snap)
        except Exception:
            # 以前这里静默吞掉：渲染一挂，界面就永远停在旧画面，
            # 而探针日志仍正常 —— 看起来像「读不到游戏数据」。打一次堆栈。
            if not getattr(self, "_render_err", False):
                self._render_err = True
                import traceback
                traceback.print_exc()
        if getattr(self, "companion", None) is not None:
            try:
                self.companion.tick()
            except Exception:
                pass
        try:
            self._after = self.root.after(int(POLL_SEC * 1000), self.poll)
        except Exception:
            pass

    def _on_battle_frame(self, snap, now):
        """一帧「在对局中」的快照：更新血量记忆，并在血量归零时定胜负。

        抽成独立方法是为了能单独测（原来这段嵌在 poll 里，测试只能调 render，
        而 render 只管画界面，测不到判定）。
        """
        seq = snap.get("seq")
        if seq is not None:
            self._last_seq = seq
        # 「换了一局」只看探针的流水号。不能按 addr：这个游戏每帧都在
        # 新建状态对象，addr 每帧都变。
        if not getattr(self, "_was_in_battle", False):
            self._battle_started = now
            self._min_life = None
            # 换局：进化点重新记初始值（试验用，见下面的 _ep_seen）
            self._ep_seen = None
            self._evm = None        # 进化状态快照（换局重置，见下面的进化事件）
            self._evf = None
        self._idle_since = 0.0
        life = snap.get("life")
        enemy = snap.get("enemy") or {}
        e_life = enemy.get("life")
        # ---- 进化点（2026-11-17 试验）：只打日志，不参与任何判定 ----
        # 探针按字段名反射读 ep/sep（实测命中 <EP>@0x3c / <SEP>@0x44），
        # 同族的次数、回合、上限也一起带上。读不到就是 None，这里什么都不打。
        try:
            _ep = (snap.get("ep"), snap.get("sep"),
                   enemy.get("ep"), enemy.get("sep"),
                   snap.get("evc"), snap.get("evt"), snap.get("sevt"),
                   enemy.get("evc"))
            old = getattr(self, "_ep_seen", None)
            if _ep != old:
                self._ep_seen = _ep
                if _ep[0] is not None or _ep[1] is not None:
                    _tail = ""
                    if _ep[4] is not None:
                        _tail += "  进化次数 %s(手动%s)" % (
                            _ep[4], snap.get("mevc"))
                    if _ep[7] is not None:
                        _tail += "  对手进化次数 %s" % _ep[7]
                    if _ep[5] is not None:
                        _tail += "  进化回合 %s" % _ep[5]
                    if _ep[6] is not None:
                        _tail += "  超进化回合 %s" % _ep[6]
                    if old is None:
                        print("进化点（初始）：我方 %s/%s  对手 %s/%s  第 %s 回合%s"
                              % (_ep[0], _ep[1], _ep[2], _ep[3],
                                 snap.get("turn"), _tail), flush=True)
                    else:
                        print("进化点：我方 %s→%s / 超进化 %s→%s"
                              "（对手 %s→%s，第 %s 回合）%s"
                              % (old[0], _ep[0], old[1], _ep[1],
                                 old[2], _ep[2], snap.get("turn"), _tail),
                              flush=True)
        except Exception:
            pass
        # ---- 进化事件（2026-11-17）：读卡对象的 EvolveState，0→1 / 0→2 ----
        # 这里只打日志；真正的「进化时给牌」在 _track_foe 里用同一个 ev 字段，
        # 不再靠「身材比卡表高 2 点」推测（那样会被徒姬的群体 +2/+2 骗）。
        try:
            for side, cards, attr in (("我方", snap.get("field_cards"), "_evm"),
                                      ("对手", enemy.get("field"), "_evf")):
                now_map = _ev_map(cards)
                prev = getattr(self, attr, None)
                setattr(self, attr, now_map)
                if prev is None:
                    continue
                for key, (cid, ev) in now_map.items():
                    was = prev.get(key, (cid, 0))[1]
                    if ev > was and ev > 0:
                        print("%s %s %s（第 %s 回合）"
                              % (side, _card_name(cid),
                                 "超进化" if ev >= 2 else "进化",
                                 snap.get("turn")), flush=True)
        except Exception:
            pass
        # 脸朝下的快照 = **不是我的数据**（手牌长度有、卡 id 全是 0，
        # 结算画面 / 对手视角都会读到这种）。它的血量尤其不能说：
        # 2026-11-12 用户实测，赢下那局之后结算画面读到一份
        # `Life=0 / Deck=12 / 手牌 3 张脸朝下`，被判成「我方生命归零」，
        # 于是 2 胜 2 负被记成 1 胜 3 负。
        # 血量、最低血、对手血都不能用这一帧的。
        face_down = bool(snap.get("faceDown"))
        if e_life is not None and not face_down:
            self._last_enemy_life = e_life
        if life is not None and not face_down:
            if getattr(self, "_min_life", None) is None:
                self._min_life = life
            self._min_life = min(self._min_life, life)
        self._was_in_battle = True
        if not face_down:
            self._last_life = life
        if not self._already_recorded(seq) and not face_down:
            if life is not None and life <= 0:
                self._record_once(seq, False, "我方生命归零（%s）" % life, now)
            elif e_life is not None and e_life <= 0:
                self._record_once(seq, True, "对手生命归零（%s）" % e_life, now)

    def _read_snapshot_worker(self):
        try:
            snap = self._snapshot()
        except Exception as e:
            snap = {"in_battle": False, "reason": str(e)[:80]}
        try:
            self.root.after(0, lambda value=snap: self._snapshot_ready(value))
        except Exception:
            with self._snapshot_lock:
                self._snapshot_busy = False

    def _snapshot_ready(self, snap):
        with self._snapshot_lock:
            self._snapshot_busy = False
        self._pending_snapshot = snap

    def _check_battle_end(self):
        """消费 frida_live 写的「对局结束」事件。

        分两种：
        - why=dead：某一方血量读到 <=0，当帧就能定胜负，直接记；
        - why=gone：状态对象消失。探针要连续 25 秒读不到我方才会发这个事件，
          所以基本就是真结束了；仍然先挂 3.5 秒，让「对局其实还在」的状态把它撤销。
        - why=next：上一局没等到尸体就开了新一局（认输后马上重开最常见），
          直接结算，不再等确认。

        双方血量都 > 0 就结束的（认输 / 掉线）判不出谁死：按血量猜谁投的降
        （血少的一方认输），打上「推定」标记 —— 顶栏 120 秒内可以一键改判，
        长考误判时还会自动撤销。

        去重靠事件里的 seq（探针的「本局流水号」）：同一局里再冒出来的结束事件
        一律不算，这样「一把游戏记了两次」不可能再发生。
        """
        try:
            path = os.path.join(HERE, "logs", "battle_end.json")
            with io.open(path, encoding="utf-8") as fh:
                d = json.load(fh)
            at = int(d.get("at") or 0)
            if at <= self._last_end_at:
                return
            self._last_end_at = at
            # 窗口启动之前留下的旧事件：只做去重，不补记
            if (at / 1000.0) < (self._opened_at - 2):
                return
            seq = d.get("seq")
            if self._already_recorded(seq):
                # 这一局已经记过。但**如果是「推定」记的，而现在拿到了硬证据**
                # （有人血量归零 / 牌库抽空），必须用硬证据把它改回来 ——
                # 用户 2026-11-11：对手长考误报一条推定负之后，同一局里真正
                # 打死对手的那个 dead 事件因为流水号相同被当成「已记录」丢掉，
                # 于是「打血赢的局没进胜率」。
                win = self._decide_end(d, allow_guess=False)
                done = False
                if win is not None:
                    try:
                        import battle_stats
                        ok, was_guess, st = battle_stats.revoke_last()
                    except Exception:
                        ok, was_guess, st = False, False, None
                    if ok and was_guess:
                        self._recorded_seq = None
                        self._guess_seq = None
                        self._last_record_at = 0
                        self._refresh_winrate(st)
                        print("这一局之前是推定的（#%s），现在有硬证据，撤掉重记"
                              % seq, flush=True)
                        done = True
                if not done:
                    print("本局（#%s）已经记过，忽略这条结束事件（%s）"
                          % (seq, d.get("why")), flush=True)
                    return
            if d.get("why") in ("dead", "next"):
                # 先看硬证据（血量归零 / 牌库抽空）。都没有时：
                #   next = 上一局没等到尸体就开了新一局（认输 / 掉线最常见）
                #   → 按血量猜谁投的降：血少的一方认输（见 _guess_by_life）。
                win = self._decide_end(d, allow_guess=False)
                guess = False
                if win is None and self._both_alive(d):
                    g = self._guess_by_life(d)
                    win, guess = (False if g is None else g), True
                if win is None:
                    return
                self._record_once(seq, win,
                                  "对局结束（%s：我方 %s / 对手 %s）%s"
                                  % ("血量归零" if d.get("why") == "dead"
                                     else "新一局已开始",
                                     d.get("me_life"), d.get("enemy_life"),
                                     ("　推定" + ("胜" if win else "负") +
                                      "（顶栏可改判）") if guess else ""),
                                  guess=guess, d=d)
                return
            # gone：挂起，等确认
            self._pending_end = {"at": time.time(), "d": d, "seq": seq}
        except FileNotFoundError:
            pass
        except Exception as e:
            # 以前这里是 except: pass，结果一次都没记上账也查不出原因。
            print("读结束事件失败：%s" % e, flush=True)

    def _settle_pending_end(self, in_battle, now, holding=False):
        """确认挂起的结束事件：对局没继续才算真的结束，然后判定胜负。"""
        pend = getattr(self, "_pending_end", None)
        if not pend:
            return
        # 「对局还在继续」必须由**新写进来的状态**证明：结束瞬间 deck_reader
        # 还会把上一次的快照当缓存返回几秒，只看 in_battle 会把真结束也撤掉。
        #
        # holding 帧不算证据：探针读不到我方时会「把上一帧再发一遍」并打上
        # hold 标记。发完 gone 之后它还会继续补发 5 秒 —— 如果拿补发帧当
        # 「对局还在继续」，结束事件永远会被撤销，兜底路径就一次也记不上账。
        # hard = 候选对象被销毁过。它不可能是「对局还在继续」—— 投降后开着
        # 新一局时，快照当然又有了，但那已经是下一局，不能拿来撤上一局的账。
        _hard = bool((pend.get("d") or {}).get("hard"))
        if in_battle and not holding and not _hard \
                and _live_mtime() > pend["at"] + 0.2:
            self._pending_end = None
            print("忽略一次假结束（对局仍在继续）", flush=True)
            return
        if now - pend["at"] < (2.5 if _hard else 6.0):
            return
        self._pending_end = None
        seq = pend.get("seq")
        if self._already_recorded(seq):
            return
        d = pend["d"]
        # gone 事件先看硬证据（血量归零 / 牌库抽空）。
        # 2026-11-06 一天里探针发过 103 次结束事件，其中 101 次双方血量都 > 0：
        # 那时的错误是**每次掉帧**都按「比血量」猜一次，一把游戏被记 12 次。
        # 现在每局只结算一次（seq 去重 + 连续 25 秒盲区才走到这里），
        # 所以可以放心按血量猜谁投的降 —— 用户 2026-11-06：投降的一方血更低。
        win = self._decide_end(d, allow_guess=False)
        guess = False
        if win is None:
            if self._looks_like_give_up(d):
                g = self._guess_by_life(d)
                win, guess = (False if g is None else g), True
                if g is None:
                    side = "双方血量相同，判不出谁投的 → 按负记"
                elif win:
                    side = "对手血更少，判为对手认输"
                else:
                    side = "我方血更少，判为我方认输"
                if d.get("hard"):
                    side += "（对象已销毁 %d 秒）" % int((d.get("blind_ms") or 0) / 1000)
            else:
                print("对象消失但双方都还活着（我方 %s/最低 %s/牌库 %s，对手 %s/最低 %s/牌库 %s），"
                      "判为掉帧，不记账"
                      % (d.get("me_life"), d.get("me_min"), d.get("me_deck"),
                         d.get("enemy_life"), d.get("enemy_min"), d.get("enemy_deck")),
                      flush=True)
                return
            self._record_once(seq, win,
                              "对局没有分出死活就结束了（我方 %s/牌库 %s，对手 %s/牌库 %s）"
                              "→ %s：%s（顶栏可改判）"
                              % (d.get("me_life"), d.get("me_deck"),
                                 d.get("enemy_life"), d.get("enemy_deck"),
                                 "胜" if win else "负", side),
                              now, guess=True, d=d)
            return
        self._record_once(seq, win,
                          "对局结束判定：%s（我方 %s/最低 %s/牌库 %s，对手 %s/最低 %s/牌库 %s）"
                          % ("胜" if win else "负", d.get("me_life"), d.get("me_min"),
                             d.get("me_deck"), d.get("enemy_life"), d.get("enemy_min"),
                             d.get("enemy_deck")), now)

    def _both_alive(self, d):
        """结束事件里双方是不是都还活着（= 没打出尸体，只可能是认输 / 掉线）。"""
        ml, el = d.get("me_life"), d.get("enemy_life")
        return isinstance(ml, int) and isinstance(el, int) and ml > 0 and el > 0

    def _guess_by_life(self, d):
        """双方都活着就结束（认输 / 掉线）：按血量猜谁投的降。

        用户 2026-11-06：**投降的一方血量应该更低** —— 打不过才投，血多是上风局，
        没必要投。所以血多的一方赢：
            我方 17 / 对手 10 → 胜（对手认输）
            我方 10 / 对手 17 → 负（我方认输）

        返回 True=胜 / False=负 / None=血量相等（或读不到），这一档判不出，
        由调用方按保守的「负」记，并照旧留「改判」按钮。

        注意这和「一局记 12 条」那个老 bug 不是同一件事：那时的错误是**每次掉帧**
        都按血量猜一次；现在每局只结算一次（seq 去重 + 25 秒盲区门槛）。
        """
        if not self._both_alive(d):
            return None
        ml, el = d["me_life"], d["enemy_life"]
        if ml == el:
            return None
        return ml > el

    def _looks_like_give_up(self, d):
        """这一局的结束看起来像「认输 / 掉线」吗（双方都活着，没有归零证据）。

        `hard` 是探针给的硬信号：**候选对象被销毁了**（投降后点「返回」、
        退出到主菜单）。长考时对象还在、只是字段不变，所以这两件事能分开 ——
        用户 2026-11-11：投降后按老逻辑要等 90 秒盲区，人早把助手关了，
        于是「投降判负」看起来完全失效。
        """
        if d.get("why") != "gone":
            return False
        if d.get("hard"):
            # hard 也不能无条件信：**新局刚开局（回合 1、双方满血）时对象本来就会
            # 整段消失**（换牌那句「保留」停着的时候）。用户 2026-11-11 就撞上
            # 这个：投降后开新局，8 秒后拿新局开局的画面记了一条 20:20 的负。
            ml, el = d.get("me_life"), d.get("enemy_life")
            if ((d.get("me_turn") or 0) <= 1
                    and isinstance(ml, int) and isinstance(el, int)
                    and min(ml, el) >= 18):
                return False
            return self._both_alive(d)
        if (d.get("blind_ms") or 0) < GONE_GUESS_MS:
            return False                    # 只盲了十几秒 —— 判为长考，不猜
        return self._both_alive(d)


    # ---------- 一局只记一次 ----------
    def _already_recorded(self, seq):
        """这一局是否已经记过账。seq 缺失（旧探针）时退回「10 秒内不重复」。"""
        if seq is None:
            if self._last_record_at and time.time() - self._last_record_at < 10:
                print("距上次记账不到 10 秒，忽略这次结束事件", flush=True)
                return True
            return False
        return self._recorded_seq is not None and seq == self._recorded_seq

    def _record_once(self, seq, win, why, now=None, guess=False, d=None):
        if self._already_recorded(seq):
            return
        self._recorded_seq = seq
        self._last_record_at = now or time.time()
        if guess:
            # 记下「这一局是推定的」：对局其实没结束（长考）时撤销，
            # 或者在顶栏手动改判。窗口过了就定案。
            #
            # 这里**不能**因为 hard 就不给撤销机会：对手长考时对象也会整段
            # 读不到，hard 一样会误报（用户 2026-11-11 下午：连着三次
            # 「我方 20 / 对手 13」这种满血局面被判成结束）。误报的账必须
            # 能撤，否则后面真正的「打死对手」会因为流水号相同被当成已记录。
            # 「新局开局被当成结束」那种误判，已经在 _looks_like_give_up 里
            # 用「回合 ≤ 1 且双方满血」挡掉了，不需要靠禁撤销来兜。
            self._guess_seq = seq
            self._guess_at = time.time()
            self._guess_d = d or {}       # 撤销时用它判断「其实还是同一局」
            self._guess_win = bool(win)
        else:
            self._guess_seq = None
            self._guess_win = None
        print(why, flush=True)
        self._record_result(win=win, guess=guess)
        # 一局结束：对手已知 / 已知衍生物立刻清空（用户 2026-11-11 的要求：
        # 「对局结束后清空」，不是「开新局时清空」）。
        # 记着是哪个流水号结束的 —— 结算画面还会继续发几帧同一局的快照，
        # 不挡住的话 _track_foe 会把这些牌又攒回来。
        self._foe_done_seq = seq
        self._foe = {"addr": None, "played": {}, "token": {}, "grant": {},
                     "ev": {}, "dead": {}, "seen": {}, "n": 0}

    def _maybe_undo_guess(self, snap):
        """推定之后对局还在继续 → 把那条「推定负」撤掉。

        用户 2026-11-06：长考超过 25 秒会被探针当成对局结束，于是记一条负。
        只要后面又有真实数据（不是探针补发的 hold 帧）而且流水号没变，
        就说明这一局还活着 —— 撤销，等真正的结束。
        """
        gs = getattr(self, "_guess_seq", None)
        if gs is None:
            return
        if time.time() - self._guess_at > FLIP_WINDOW:
            self._guess_seq = None          # 窗口过了，定案
            return
        seq = snap.get("seq")
        if seq is None:
            return
        if seq != gs:
            # 探针发完 gone 会把上一帧清掉，数据回来时它以为是「新的一局」，
            # 流水号 +1（2026-11-06 12:5x 就是这样：长考误记负之后流水号从
            # …001 跳到 …002，撤销条件对不上，那条负就一直留着）。
            # 所以再用牌库/血量/回合复核一次：确实还是同一局才撤销。
            d = getattr(self, "_guess_d", None) or {}
            if not self._same_battle(snap, d):
                # 真的换了新的一局：推定结果**定案**，改判按钮收起来。
                # 用户 2026-11-13：上一局对手投降（推定胜）→ 开新局后按钮还在，
                # 而且那条胜被当成「同一局还活着」撤掉了 → 8 胜 1 负变 7 胜 2 负。
                self._guess_seq = None
                print("新的一局开始了，上一局的推定结果定案（不再改判）", flush=True)
                return
        if getattr(self, "_guess_undone", None) == gs:
            return                          # 同一局只撤一次，避免来回跳
        if getattr(self, "_recorded_seq", None) != gs:
            # 这中间已经记了别的对局：那条负早被后面的一局顶掉了，别乱撤
            self._guess_seq = None
            return
        try:
            import battle_stats
            ok, _was_guess, st = battle_stats.revoke_last()
        except Exception:
            ok, st = False, None
        self._guess_seq = None
        self._guess_undone = gs
        self._recorded_seq = None           # 允许这一局后面按真实结果重记
        self._foe_done_seq = None           # 这条也是误判：对手栏不该被清掉
        if ok and st is not None:
            self._refresh_winrate(st)
        _tag = ("负" if self._guess_win is False
                else "胜" if self._guess_win is True else "")
        print("对局其实还在继续（流水号 #%s），撤销刚才那条「推定%s」"
              % (seq, _tag), flush=True)
        self._guess_win = None

    def _same_battle(self, snap, d):
        """流水号变了之后，这张快照看起来还是「推定那一局」吗？

        只有**不可能属于新局**的情况才认：
          · 探针明确说「刚换到新的一局」（newGame）→ 不是同一局；
          · 牌库比结束那一刻多 → 新局（牌库对局中只会减少，新局一定回满）；
          · 回合数比结束那一刻小 → 新局（新局从第 1 回合开始）；
          · 双方都还活着（死了一个就不是「还在打」）。

        2026-11-13 用户实测的错判：对手投降 → 推定胜；开新局后牌库 35（旧局 33）、
        回合 1（旧局 2），老判据「<= 旧牌库+2」和「回合没退回 2 以上」都成立，
        于是把那条胜当成「同一局还活着」撤掉了 —— 8 胜 1 负变成 7 胜 2 负。
        """
        if snap.get("newGame"):
            return False
        mine = snap.get("deckCount")
        old_deck = d.get("me_deck")
        if mine is None or old_deck is None:
            return False
        if mine > old_deck:
            return False
        turn_now = snap.get("turn")
        turn_old = d.get("me_turn")
        if isinstance(turn_now, int) and isinstance(turn_old, int):
            if turn_now < turn_old:
                return False
            if turn_now <= 1 and turn_old <= 1 and mine >= old_deck:
                # 开局阶段：回合没动、牌库没少 —— 分不清是新局还是长考。
                # 宁可让推定结果定案，也不要撤掉刚记的账（用户 2026-11-13）。
                return False
        if (snap.get("life") or 0) <= 0:
            return False
        e_life = (snap.get("enemy") or {}).get("life")
        if e_life is not None and e_life <= 0:
            return False
        return True

    def _flip_last(self):
        """顶栏「改判」：把刚记的那一条胜↔负。"""
        try:
            import battle_stats
            ok, st = battle_stats.flip_last()
            if ok:
                self._refresh_winrate(st)
                print("已改判上一局结果", flush=True)
        except Exception as e:
            print("改判失败：%s" % e, flush=True)
        self._guess_seq = None
        try:
            self.render(self._last or {})
        except Exception:
            pass

    def _decide_end(self, d, allow_guess=False):
        """按结束事件里的数据判定胜负，判不出返回 None。

        顺序：血量归零 > 牌库抽空 > 双方都没死（投降/断线）。
        最后一条**不再按血量猜**：投降的一方血量往往更高，猜出来一半是反的
        （2026-11-06 一局记了 12 次账就是这个来源）。现在调用方一律传
        allow_guess=False，判不出就交给「双方都活着 ⇒ 记负（推定）」那条路。
        保留 allow_guess 参数只是为了回归测试能复现旧行为、说明它确实会记错。
        """
        ml, el = d.get("me_life"), d.get("enemy_life")
        mm, em = d.get("me_min"), d.get("enemy_min")
        md, ed = d.get("me_deck"), d.get("enemy_deck")
        if isinstance(em, int) and em <= 0:
            return True
        if isinstance(el, int) and el <= 0:
            return True
        if isinstance(mm, int) and mm <= 0:
            return False
        if isinstance(ml, int) and ml <= 0:
            return False
        if isinstance(md, int) and md <= 0:
            return False                    # 我先抽空牌库
        if isinstance(ed, int) and ed <= 0:
            return True                     # 对手先抽空牌库
        if not allow_guess:
            return None                     # gone 事件：没有硬证据就不猜
        if isinstance(ml, int) and isinstance(el, int) and ml != el:
            return ml > el
        return None


    def _record_result(self, win, guess=False):
        """记录一局胜负并刷新胜率显示。"""
        try:
            import battle_stats
            st = battle_stats.record(win, guess=guess)
            self._refresh_winrate(st)
        except Exception:
            pass

    def _refresh_winrate(self, st=None):
        """刷新胜率数据。界面数字由 deck_hud 画，这里只维护 self._stats。"""
        try:
            if st is None:
                import battle_stats
                st = battle_stats.stats()
            wins = int(st.get("wins") or 0)
            losses = int(st.get("losses") or 0)
            if wins + losses:
                self._stats = {"win": wins, "loss": losses,
                               "rate_txt": "%.0f%%" % st.get("rate", 0)}
            else:
                self._stats = {"win": 0, "loss": 0, "rate_txt": "0%"}
        except Exception:
            self._stats = {"win": 0, "loss": 0, "rate_txt": "0%"}
        try:
            self.render(self._last or {})
        except Exception:
            pass

    def _reset_winrate(self):
        """清空胜/负记录。二次确认由顶栏「重置」按钮自己弹（见 deck_hud）。"""
        try:
            import battle_stats
            self._refresh_winrate(battle_stats.reset())
            print("战绩已清空", flush=True)
        except Exception as e:
            print("清空战绩失败：%s" % e, flush=True)

    def _snapshot(self):
        import deck_reader
        return deck_reader.snapshot()

    # ---------------- 陪玩 ----------------
    def _on_ai_text(self, text):
        self._bubble = (str(text), time.time() + 9.0)
        try:
            self.root.after(0, lambda: self.render(self._last or {}))
        except Exception:
            pass

    def _on_ai_state(self, text):
        self._on_ai_text(text)

    def _open_ai(self):
        try:
            import companion
        except Exception:
            return
        cfg = companion.load_config()
        win = tk.Toplevel(self.root)
        win.title("AI 陪玩设置")
        win.configure(bg=BG)
        try:
            win.geometry("+%d+%d" % (self.root.winfo_x() + 40,
                                     self.root.winfo_y() + 60))
            win.attributes("-topmost", True)
        except Exception:
            pass
        pad = tk.Frame(win, bg=BG)
        pad.pack(fill="both", expand=True, padx=12, pady=10)
        vars_ = {}

        def row(label, key, show=""):
            f = tk.Frame(pad, bg=BG)
            f.pack(fill="x", pady=2)
            tk.Label(f, text=label, bg=BG, fg=GOLD2, font=self.f_s,
                     width=8, anchor="w").pack(side="left")
            v = tk.StringVar(value=str(cfg.get(key) or ""))
            tk.Entry(f, textvariable=v, bg=BG2, fg=FG, insertbackground=FG,
                     relief="flat", font=self.f_s, show=show).pack(
                side="left", fill="x", expand=True)
            vars_[key] = v

        row("API Key", "api_key", show="*")
        row("Base URL", "base_url")
        row("模型", "model")

        ttsv = tk.BooleanVar(value=bool(cfg.get("tts_enabled")))
        f1 = tk.Frame(pad, bg=BG)
        f1.pack(fill="x", pady=3)
        tk.Checkbutton(f1, text="语音（Edge TTS，云端）", variable=ttsv,
                       bg=BG, fg=FG, selectcolor=BG2, activebackground=BG,
                       font=self.f_s).pack(side="left")

        f2 = tk.Frame(pad, bg=BG)
        f2.pack(fill="x", pady=2)
        tk.Label(f2, text="音色", bg=BG, fg=GOLD2, font=self.f_s,
                 width=8, anchor="w").pack(side="left")
        voice_names = [n for _, n in companion.VOICES]
        voice_ids = [v for v, _ in companion.VOICES]
        cur = cfg.get("voice") or voice_ids[0]
        vv = tk.StringVar(value=voice_names[voice_ids.index(cur)]
                          if cur in voice_ids else voice_names[0])
        om = tk.OptionMenu(f2, vv, *voice_names)
        om.config(bg=BG2, fg=FG, relief="flat", bd=0, font=self.f_s,
                  highlightthickness=0)
        om["menu"].config(bg=BG, fg=FG, activebackground=BG2,
                          activeforeground=GOLD, font=self.f_s)
        om.pack(side="left", fill="x", expand=True)

        enabledv = tk.BooleanVar(value=bool(cfg.get("enabled")))
        f3 = tk.Frame(pad, bg=BG)
        f3.pack(fill="x", pady=3)
        tk.Checkbutton(f3, text="启用陪玩（对局中自动说话）", variable=enabledv,
                       bg=BG, fg=FG, selectcolor=BG2, activebackground=BG,
                       font=self.f_s).pack(side="left")

        def do_save():
            cfg["api_key"] = vars_["api_key"].get().strip()
            cfg["base_url"] = vars_["base_url"].get().strip() or cfg["base_url"]
            cfg["model"] = vars_["model"].get().strip() or cfg["model"]
            cfg["tts_enabled"] = bool(ttsv.get())
            cfg["enabled"] = bool(enabledv.get())
            idx = voice_names.index(vv.get()) if vv.get() in voice_names else 0
            cfg["voice"] = voice_ids[idx]
            companion.save_config(cfg)
            if self.companion is not None:
                self.companion.cfg = companion.load_config()
            win.destroy()

        def do_test():
            if self.companion is None:
                return
            self.companion.cfg = companion.load_config()
            self.companion.test_tts()

        bf = tk.Frame(pad, bg=BG)
        bf.pack(fill="x", pady=(6, 0))
        for txt, cmd in (("保存", do_save), ("测试语音", do_test),
                         ("关闭", win.destroy)):
            tk.Button(bf, text=txt, command=cmd, bg=BG2, fg=GOLD,
                      relief="flat", bd=0, padx=10, pady=3, font=self.f_s,
                      activebackground=BG, cursor="hand2").pack(
                side="left", padx=(0, 6))

    # ---------------- 渲染 ----------------
    def _winrate_vm(self):
        st = self._stats or {}
        return {"win": st.get("win", 0), "loss": st.get("loss", 0),
                "rate_txt": st.get("rate_txt", "0%"),
                "can_flip": self._can_flip()}

    def _can_flip(self):
        """最近一条是不是「推定」结果、还能不能改判。"""
        if getattr(self, "_guess_seq", None) is None:
            return False
        return (time.time() - getattr(self, "_guess_at", 0.0)) < FLIP_WINDOW

    def _bubble_vm(self):
        text, until = self._bubble
        return str(text)[:22] if (text and time.time() < until) else ""

    def _resolve_pp(self, raw_pp, raw_max, turn):
        """把「内存里的 PP」和「按回合数推算的 PP」合成一个可信值。

        2026-11-06 用户反馈：一局打完后 PP 还停在旧值，进新对局也不重置。
        根本原因是 PP 的内存偏移是猜的（探针里 pp 和 maxpp 读的还是同一个地址），
        整局都读出 5。所以：
          - 只有「本局内这个值变动过 2 次以上」才说明确实是 PP，采信它；
          - 否则按回合数算：影之诗第 N 回合双方各 N 点，上限 10。
            这样新对局第一回合一定从 1/1 开始，不会再继承上一局。
        """
        obs = self._pp_obs
        # 换牌阶段回合数可能是 0，也按第 1 回合算（这时就是 1 点 PP）
        est = (min(max(turn, 1), 10)
               if (turn is not None and 0 <= turn <= 30) else None)
        if est == 1:
            obs["n"] = 0          # 新对局：上一局「内存值可信」的结论作废
        if raw_pp is not None and 0 <= raw_pp <= 10:
            if obs["last"] is not None and raw_pp != obs["last"]:
                obs["n"] += 1
            obs["last"] = raw_pp
        else:
            obs["last"] = None
        if obs["n"] >= 2 and raw_pp is not None and 0 <= raw_pp <= 10:
            cur = raw_pp
            cap = est if est is not None else raw_pp
            # 只有确认「内存里这个值会动」之后，才敢信它的上限字段
            if raw_max is not None and cap <= raw_max <= 10:
                cap = raw_max
        elif est is not None:
            cur = cap = est
        elif raw_pp is not None and 0 <= raw_pp <= 10:
            cur = cap = raw_pp
        else:
            return None, None
        return cur, max(cap, cur)

    def render(self, snap):
        self._last = snap
        # 不是在对局里时，读取层会给一句 reason（等文件 / 等开局 / 读取没在跑）。
        # 光看「当前卡组」分不出「你还没开打」和「读取根本没起来」，
        # 把后者标在顶栏上，省得以为记牌器坏了（用户 2026-11-06）。
        _reason = str(snap.get("reason") or "")
        # 从「对局视图」切回「当前卡组」要延迟 2 秒：读取器偶尔会有一两帧报
        # idle（对象正在重建 / 结算画面），立刻切换会让整段列表换一份内容，
        # 再换回来 —— 看起来就是「卡牌消失一下又出现」。
        if not snap.get("in_battle"):
            _now = time.time()
            if (not getattr(self, "_pre_battle", True)
                    and getattr(self, "_has_content", False)):
                _since = getattr(self, "_idle_at", 0.0)
                if not _since:
                    self._idle_at = _now
                    return
                if _now - _since < 2.0:
                    return
        else:
            self._idle_at = 0.0

        if not snap.get("in_battle"):
            # 用户 2026-10-29：选好卡组后、还没进对局时也要显示卡组
            item = None
            try:
                import deck_reader
                decks = deck_reader._decks_from_file()
                active = deck_reader._active_name()
                item = next((x for x in decks if x.get("name") == active),
                            decks[0] if decks else None)
            except Exception:
                item = None
            if not item:
                reason = str(snap.get("reason") or "")[:24]
                if (getattr(self, "_has_content", False)
                        and time.time() - getattr(self, "_content_at", 0.0) < 3.0):
                    return          # 短暂读不到：先保留列表，别清空
                vm = {"mode": "idle", "deck_left": None, "hand_n": None,
                      "status": ("未在对战中 · " + reason) if reason else "未在对战中",
                      "sections": [], "bar": 0, "pp_txt": "", "washed": False}
                vm.update(self._winrate_vm())
                self.hud.set_vm(vm)
                self._has_content = False
                return
            from collections import Counter
            rows = [(cid, n, n, 0)
                    for cid, n in Counter(item["cards"]).items()]
            snap = {"in_battle": True, "deckCount": len(item["cards"]),
                    "hand": [], "played": [], "destroyed": [],
                    "rows": rows, "total": len(item["cards"]),
                    "overdraw": 0, "returned": False,
                    # 这不是真快照，是「当前卡组」预览：里面没有对手字段。
                    # 不标出来的话 _track_foe 会当成「对手已打出归零 = 换局」，
                    # 把累积的对手牌和衍生物整个清掉 —— 于是每次回预览（长考、
                    # 结算、idle 抖一下）统计就得从头再来（用户 2026-11-06：
                    # 「衍生物模块必须切到对方才开始统计」）。
                    "preview": True}
            self._pre_name = item["name"]
            self._pre_battle = True
        else:
            self._pre_battle = False
            self._pre_name = None

        try:
            import deck_reader
            meta = deck_reader.load_meta()
        except Exception:
            meta = {}

        def info(cid):
            try:
                return deck_reader.card_meta(cid)
            except Exception:
                return meta.get(str(cid)) or {}

        def name_of(cid):
            return info(cid).get("name") or ("#%d" % cid)

        CLS_ORDER = {1: 0, 2: 1, 3: 2, 6: 3, 4: 4, 5: 5, 7: 6, 0: 7}
        CLS_COLOR = {1: "#2e8b57", 2: "#e8c547", 3: "#3b6fd4", 6: "#e8e8e8",
                     4: "#e8843c", 5: "#8b2f4a", 7: "#3fb5a8", 0: "#808080"}
        # 卡表 type：1=随从、2/3=护符、4=法术
        TYPE_TXT = {1: "随从", 2: "护符", 3: "护符", 4: "法术"}

        def sort_key(cid):
            m = info(cid)
            # 最后加 cid 作为 tie-breaker，保证排序稳定（否则同名卡顺序会抖）
            return (m.get("cost") or 0, CLS_ORDER.get(m.get("class"), 9),
                    m.get("rarity") or 0, name_of(cid), cid)

        rows = snap.get("rows") or []
        # 手牌槽位里可能有空位（读出来是 0），这种不算牌，直接丢掉 ——
        # 否则手牌区会冒出一行「#0」。
        hand_raw = list(snap.get("hand") or [])
        # 2026-11-11 用户第二次反馈「手牌分区消失」后的最后一道保险：
        # 探针认人偶尔会被对手的一份快照骗过去，而对手的手牌内容读不到
        # （长度有、卡 id 全是 0），过滤完就成了空数组 —— 手牌整段不渲染。
        # 探针那边已经用「脸朝下」把它们挡在「谁是我」之外；这里再兜一层：
        # 带 faceDown 的帧一律沿用上一帧的手牌，宁可晚一帧也不让分区消失。
        if snap.get("faceDown") and getattr(self, "_last_hand", None):
            hand_raw = self._last_hand
        else:
            self._last_hand = hand_raw
        hand = [c for c in hand_raw if isinstance(c, int) and c > 0]
        # 牌库段：还在牌库的卡，显示剩余张数（抽上手就减数字，不直接删行）
        deck_items = [(r[0], int(r[2] or 0)) for r in rows if r[2] > 0]
        deck_items.sort(key=lambda x: sort_key(x[0]))       # 1.1：按费用排序
        # 手牌：相同卡名合并成一行 + 数量
        hand_by_name = {}
        for c in hand:
            nm = name_of(c)
            if nm not in hand_by_name:
                hand_by_name[nm] = [c, 0]
            hand_by_name[nm][1] += 1
        hand_items = [(cid, cnt) for cid, cnt in hand_by_name.values()]
        hand_items.sort(key=lambda x: sort_key(x[0]))

        from collections import Counter
        over_items = sorted(
            Counter(c for c in (snap.get("overdraw_cards") or []) if c).items(),
            key=lambda x: sort_key(x[0]))
        # 换牌阶段不显示手牌（用户 2026-10-31：换牌结束才显示）
        mulligan_done = bool(snap.get("mulliganDone", True))
        if not mulligan_done:
            hand_items = []

        # ---- 能量点（PP）----
        # 旧实现直接显示内存里那个字段，可它的偏移是猜的：整局都读出 5，
        # 打完一局也不重置，界面永远停在 5/5（用户 2026-11-06 反馈）。
        def _int_or_none(v):
            try:
                return int(v) if v is not None else None
            except Exception:
                return None

        pp, maxpp = self._resolve_pp(_int_or_none(snap.get("pp")),
                                     _int_or_none(snap.get("maxpp")),
                                     _int_or_none(snap.get("turn")))

        def mk(cid, qty, sec):
            m = info(cid)
            cost = m.get("cost") or 0
            return {"cid": cid, "name": name_of(cid), "cost": cost,
                    "kind": TYPE_TXT.get(m.get("type"), ""),
                    # 职业色条：对局中和「当前卡组」预览都要显示
                    # （用户 2026-11-06：预览状态下卡牌前的颜色标识不见了）。
                    # 以前预览时故意不发色条，怕和「可打出」的金边混淆 —— 但预览里
                    # 本来就没有可打出的牌，那条金边永远不会出现，纯属多虑。
                    "cls_color": CLS_COLOR.get(m.get("class")),
                    "qty": int(qty), "foe": sec == "foe",
                    "playable": (sec == "hand" and pp is not None
                                 and cost <= pp),
                    "zero": False}

        remaining = int(snap.get("deckCount") or snap.get("remaining") or 0)
        total = int(snap.get("total") or 0) or (remaining + len(hand))
        washed = bool(snap.get("returned"))

        sections = []
        # 手牌排在最前面（V1.0）：一局里最需要实时盯的是「手上有什么、能打哪张」，
        # 牌库那 40 行是参考信息，往下滚就行。手牌分区可折叠，占地方就点标题收起。
        if SHOW_HAND and not mulligan_done:
            sections.append({"key": "hand", "title": "手牌", "side": "self",
                             "note": "", "foldable": True, "items": [],
                             "empty": "换牌中"})
        elif SHOW_HAND and hand_items:
            playable_n = (sum(1 for cid, _ in hand_items
                              if (info(cid).get("cost") or 0) <= pp)
                          if pp is not None else 0)
            sections.append({"key": "hand", "title": "手牌", "side": "self",
                             "note": ("可打出 %d" % playable_n
                                      if playable_n else str(len(hand))),
                             "foldable": True,
                             "items": [mk(cid, q, "hand")
                                       for cid, q in hand_items[:40]]})
        if deck_items:
            sections.append({"key": "deck", "title": "牌库", "side": "self",
                             "note": str(remaining), "foldable": True,
                             "items": [mk(cid, q,
                                          "preview" if self._pre_battle else "deck")
                                       for cid, q in deck_items[:80]]})

        try:
            grant_rows, foe_rows, foe_note = self._track_foe(
                snap, info, name_of, TYPE_TXT, sort_key)
        except Exception:
            grant_rows, foe_rows, foe_note = [], [], ""
        # 「已知衍生物」排在「对手已知」上面（用户 2026-11-11 指定的位置）：
        # 它回答的是“对手手上可能多了什么”，比“已经打出过什么”更该先看到。
        if grant_rows:
            sections.append({"key": "deriv", "title": "已知衍生物",
                             "side": "foe", "note": "", "tone": "foe",
                             "items": grant_rows})
        if foe_rows:
            sections.append({"key": "foe", "title": "对手已知", "side": "foe",
                             "note": foe_note, "tone": "foe",
                             "items": foe_rows})
        if not foe_rows and not grant_rows and sections:
            # 对方标签下什么都没有时给一行提示，别让那块空成黑屏。
            # 只在「已经有内容」时才加：否则空数据防抖会以为读到了东西。
            sections.append({"key": "foe", "title": "对手已知", "side": "foe",
                             "note": "", "tone": "foe", "items": [],
                             "empty": "还没有看到对手出牌"})

        if over_items:
            sections.append({"key": "over", "title": "爆牌", "side": "self",
                             "note": "",
                             "items": [mk(cid, q, "over")
                                       for cid, q in over_items]})

        # ---- 空数据防抖：读取器换对象 / 结算画面时会有一两帧读不到东西
        if not sections:
            if (getattr(self, "_has_content", False)
                    and time.time() - getattr(self, "_content_at", 0.0) < 6.0):
                if not getattr(self, "_holding", False):
                    self._holding = True
                    print("列表数据短暂为空：保留上一帧内容，不重建", flush=True)
                return
        self._holding = False

        if getattr(self, "_pre_battle", False):
            mode = "preview"
            hint = ""
            if "没找到游戏进程" in _reason:
                hint = " · 没找到游戏进程"
            elif "游戏还没进页面" in _reason:
                hint = " · 游戏还没进页面"
            elif "正在连接游戏" in _reason:
                hint = " · 正在连接游戏"
            elif "读取启动失败" in _reason:
                hint = " · 读取启动失败"
            elif "读取已断" in _reason:
                hint = " · 读取已断"
            elif "等待常驻读取" in _reason:
                hint = " · 读取未启动"
            elif "实时读取未在跑" in _reason:
                hint = " · 读取已停"
            status = "当前卡组 · %s%s" % (self._pre_name or "", hint)
            deck_left = None
        else:
            mode = "battle"
            status = self._bubble_vm()
            deck_left = remaining

        # 进化点（我方）：探针按字段名反射读到才显示，读不到就整段不画
        ev_txt = ""
        try:
            _ep, _sep = snap.get("ep"), snap.get("sep")
            _mep, _msep = snap.get("maxep"), snap.get("maxsep")
            if _ep is not None and _mep:
                ev_txt = "%d/%d" % (_ep, _mep)
                if _sep is not None and _msep:
                    ev_txt += "·超%d/%d" % (_sep, _msep)
        except Exception:
            ev_txt = ""

        vm = {"mode": mode, "deck_left": deck_left, "hand_n": len(hand),
              "pp_txt": ("%d/%d" % (pp, maxpp or pp))
                        if pp is not None else "",
              "ev_txt": ev_txt,
              "bar": (remaining / float(total)) if total else 0,
              "art": art_on(),
              "washed": washed, "status": status, "sections": sections}
        vm.update(self._winrate_vm())
        self.hud.set_vm(vm)
        self._has_content = bool(sections)
        self._content_at = time.time()

    def _foe_frame_dirty(self, snap, enemy):
        """这一帧的「对手」八成其实是我方 —— 别往对手牌池里加东西。

        用户 2026-11-06 三次反馈：对手已知里混进我方打出的牌和我方衍生物。
        探针日志实锤：同一帧里「我方匹配 12 / 对手匹配 36」连续几十帧，身份认反了，
        我方的 played 就被当成对手的。探针那边已经改成「只在卡组重合分最高的
        一档里挑人」，这里再兜一层，防住已经启动的老探针。

        三个一眼可见的脏特征（宁可少记一张，也不能串人）：
          1. 同一帧我方和对手是同一个对象 —— 不可能；
          2. 我方牌库读不到（None）—— 这一帧根本没认出我方，对手是谁更说不准；
          3. 我方手牌读到了但全是 0 —— 字段读歪了。
        """
        addr = snap.get("addr")
        e_addr = enemy.get("addr")
        if addr is not None and e_addr is not None and addr == e_addr:
            return True
        if snap.get("preview"):
            # 「当前卡组」预览用的假快照，压根没有对手数据：
            # 更新它就是「对手已打出归零 = 换局」，会把池子清空。
            return True
        if snap.get("deckCount") is None:
            return True
        hand = snap.get("hand") or []
        if hand:
            try:
                if not any(int(c or 0) for c in hand):
                    return True
            except Exception:
                pass
        return False

    # ---------------- 对手已见牌 ----------------
    # ---------------- 对手已见牌 ----------------
    def _track_foe(self, snap, info, name_of, TYPE_TXT, sort_key):
        """对手已知 = 对手打出的牌 + 召唤到战场上的 token +「获得」推测。

        V1.0 起不再有「衍生物」这个分类：token 也按卡表 type 归进
        【随从】【法术】【护符】三类（用户 2026-11-08 要求）。卡表里查不到
        type 的特殊对象（纹章、加护、特性……）就不显示类型，只留卡名。
        「衍生物」独立一栏早在 1.1.0 就作废了（读游戏自己的对战记录跟不下来）。

        「获得 · XX」（2026-11-11）：对手手牌内容读不到，但很多卡的技能原文
        写死了“将1张『某某』加入手牌”。这类在对手打出那张卡时补一行，
        类型栏写「推测·随从」灰色显示，和真读到的牌区分开（见 foe_grants）。
        """
        from collections import Counter
        # 这一局已经结算过了（记过账）→ 对手这两栏立刻清空，不用等新局。
        # 2026-11-11 用户：对局结束后「已知衍生物 / 对手已知」一直留着，
        # 要开下一局才消失。原因是**结算画面里对象还在**，探针照常发快照，
        # 记牌器根本不知道已经结束了（它只在流水号变了时才清）。
        seq_now = snap.get("seq")
        done = getattr(self, "_foe_done_seq", None)
        if done is not None:
            if seq_now is not None and seq_now == done:
                return [], [], ""
            self._foe_done_seq = None       # 新的一局了，照常积累
        st = self._foe
        for k in ("played", "token", "grant", "ev", "dead", "seen"):
            # 老实例（热更新前的）兼容：grant=推测获得，ev=已记为“进化过”的随从，
            # dead=已记为进过墓地的，seen=上过场/打出过的（谢幕曲的判定前提）
            st.setdefault(k, {})
        st.pop("back", None)               # 撤掉独立栏后残留的键
        enemy = snap.get("enemy") or {}

        def ids(key):
            out = []
            for c in (enemy.get(key) or []):
                if isinstance(c, dict):        # 战场元素可能是 {cardId, atk, life}
                    c = c.get("cardId")
                try:
                    c = int(c)
                except Exception:
                    continue
                # 卡 id 都是 8 位左右；读歪一位会拿到几亿的垃圾数，挡掉
                if 1000000 <= c <= 999999999:
                    out.append(c)
            return out

        def field_stats():
            """对手场上的 (卡 id, 攻, 血, 进化状态, 实例号)。老探针不送时给 None。"""
            out = []
            for c in (enemy.get("field") or []):
                if not isinstance(c, dict):
                    continue
                try:
                    cid = int(c.get("cardId") or 0)
                except Exception:
                    continue
                if 1000000 <= cid <= 999999999:
                    out.append((cid, c.get("atk"), c.get("life"),
                                c.get("ev") or 0, c.get("uid")))
            return out

        def mk(c, k, ev=0):
            m = info(c)
            return {"cid": c, "name": name_of(c),
                    "cost": m.get("cost") or 0,
                    "kind": TYPE_TXT.get(m.get("type"), ""),
                    "qty": int(k), "foe": True, "playable": False,
                    "zero": False, "ev": int(ev or 0)}

        def ev_counts():
            """每个卡 id 各有几张「进化 / 超进化」——按**历史实例**数，不是当前场面。

            用户 2026-11-17：对手手动进化的随从被击杀后，标记不能消失。
            所以这里统计的是「这局见过哪些实例进化过」（st["ev"]，按 uid 记，
            只在换局时清空），已经死掉的实例照样算一份。
            """
            c1, c2 = Counter(), Counter()
            for v in (st.get("ev") or {}).values():
                # v 现在是 (cid, ev)；老数据可能是裸 int，跳过即可
                if isinstance(v, (tuple, list)) and len(v) >= 2:
                    cid, ev = int(v[0]), int(v[1] or 0)
                else:
                    continue
                if ev >= 2:
                    c2[cid] += 1
                elif ev == 1:
                    c1[cid] += 1
            return c1, c2

        def build():
            c1, c2 = ev_counts()

            def split_ev(c, k):
                """把「这张牌 k 张」按场上实例的进化状态拆成 超/普/未 三行。

                进化状态只有场上的实例才有；已经死掉/离开场面的那些算「未进化」
                （它们就算进化过也不在场面上了，角标没有意义）。
                """
                k2 = min(k, c2.get(c, 0))
                k1 = min(k - k2, c1.get(c, 0))
                k0 = k - k2 - k1
                out = []
                for evv, kk in ((2, k2), (1, k1), (0, k0)):
                    if kk > 0:
                        out.append(mk(c, kk, evv))
                return out

            rows = []
            by_name = {}
            for c, k in sorted(st["played"].items(), key=lambda x: sort_key(x[0])):
                for row in split_ev(c, k):
                    # 同一张 token 在卡表里有 90 / 95 两个镜像 id，按名并成一行，
                    # 免得列表里出现两条一模一样的卡名。进化状态不同的不合并。
                    if is_token_card(c):
                        old = by_name.get((row["name"], row["ev"]))
                        if old is not None:
                            old["qty"] += row["qty"]
                            continue
                        by_name[(row["name"], row["ev"])] = row
                    rows.append(row)
            tok = {}
            for c in sorted(st["token"], key=lambda x: sort_key(x)):
                if c in st["played"]:
                    continue           # 已打出过的上面那轮已经列了，别记两遍
                for row in split_ev(c, st["token"][c] or 1):
                    old = tok.get((row["name"], row["ev"]))
                    if old is not None:
                        old["qty"] += row["qty"]
                        continue
                    tok[(row["name"], row["ev"])] = row
                    rows.append(row)
            # 「已知衍生物」：从技能原文推出来的（对手手牌内容读不到）。
            # 用户 2026-11-11：**单独一栏**，不再混进「对手已知」。
            #   · 对手真把同名卡打出来时，这一栏**既不扣数量也不移除** ——
            #     “这张是他从哪张牌上拿到的”本身就是想留住的信息；
            #   · 换局（下面 played 变少那一段）会把 st["grant"] 整个清掉，
            #     所以一局结束这一栏自然就空了。
            # 类型栏仍然写「推测·随从」（灰），和真读到的牌明确区分。
            gr = {}
            for c in sorted(st["grant"], key=lambda x: sort_key(x)):
                row = mk(c, st["grant"][c] or 1)
                row["grant"] = True
                row["kind"] = (FOE_TAG + "·" + row["kind"]) \
                    if row["kind"] else FOE_TAG
                old = gr.get(row["name"])
                if old is not None:
                    old["qty"] += row["qty"]
                    continue
                gr[row["name"]] = row
            hand_n = int(enemy.get("hand_n") or 0)
            note = ("手牌 %d" % hand_n) if hand_n else ""
            # 对手的进化点：和我方一样，读得到才写（2026-11-17）
            try:
                _e_ep, _e_sep = enemy.get("ep"), enemy.get("sep")
                _e_mx, _e_msx = enemy.get("maxep"), enemy.get("maxsep")
                if _e_ep is not None and _e_mx:
                    bit = "进化 %d/%d" % (_e_ep, _e_mx)
                    if _e_sep is not None and _e_msx:
                        bit += "·超%d/%d" % (_e_sep, _e_msx)
                    note = (note + " · " + bit) if note else bit
            except Exception:
                pass
            return [gr[k] for k in gr], rows, note

        # 这一帧的「对手」八成其实是我方 → 只回放已有统计，不往池子里加东西。
        # 宁可少记一张，也不能把用户自己的牌算到对手头上。
        if self._foe_frame_dirty(snap, enemy):
            return build()

        played = ids("played")
        field_ids = ids("field")
        board = field_ids + ids("destroyed")
        n = len(played)
        # 换局（= 这一局结束）有两个判据：
        #   ① 已打出的张数变少（老判据）；
        #   ② 探针的流水号变了 —— 它只在真换局时 +1，比张数可靠：
        #      上一局对手一张都没出就投降时，played 一直是 0，① 永远不成立，
        #      「已知衍生物」就会带着上一局的牌进新局。
        seq = snap.get("seq")
        new_round = (n + 1 < st.get("n", 0))
        if (not new_round and seq is not None
                and st.get("seq") is not None and seq != st["seq"]):
            new_round = True
        if seq is not None:
            st["seq"] = seq
        if new_round:
            st["played"] = {}
            st["token"] = {}
            st["grant"] = {}
            st["ev"] = {}
            st["dead"] = {}                    # 已记为“死了”的卡
            st["seen"] = {}                    # 上过场 / 打出过的卡
            st["primed"] = 0
            st["n"] = 0
        st["n"] = n
        # 谢幕曲只可能对“上过场”的牌发动：弃牌、爆牌、被弃到墓地的都没触发
        for c in played + field_ids:
            st["seen"][c] = 1
        # 打开记牌器时这局可能已经打了一半：先把墓地里的旧牌登记成“已死”，
        # 不补账，否则一开窗就冒出一堆早就结算过的谢幕曲。
        if not st.get("primed"):
            st["primed"] = 1
            for c in ids("destroyed"):
                st["dead"][c] = 1
        for c, k in Counter(played).items():
            if k > st["played"].get(c, 0):
                st["played"][c] = k
                # 入场曲：打出就结算，写在技能原文里的「加入手牌」直接记上
                for tid, cnt in on_play(c):
                    st["grant"][tid] = int(st["grant"].get(tid, 0)) + cnt
        # 谢幕曲：这张牌第一次出现在墓地，而且之前确实上过场 → 补账
        for c in ids("destroyed"):
            if st["dead"].get(c):
                continue
            st["dead"][c] = 1
            if not st["seen"].get(c):
                continue
            for tid, cnt in on_death(c):
                st["grant"][tid] = int(st["grant"].get(tid, 0)) + cnt
        for c, k in Counter(board).items():
            if c in st["played"]:
                continue            # 已打出过的上面那轮已经列了，别记两遍
            if k > st["token"].get(c, 0):
                st["token"][c] = k
        # 进化时 / 超进化时（2026-11-17 改写）：读卡对象的 <EvolveState>
        #   0 = 未进化   1 = 进化   2 = 超进化
        # 以前是「攻防比卡表高 2 点就当进化」，会被「徒姬超进化给其他梦魇
        # 随从 +2/+2」这类群体 buff 骗过去（用户 2026-11-17 指出），现在直接
        # 看状态位，buff 永远是 0，不会再误判。
        for cid, atk, life, ev, uid in field_stats():
            key = uid if uid else ("c%d" % cid)
            old = st["ev"].get(key)
            if old and isinstance(old, (tuple, list)) and old[1] >= (ev or 0):
                continue
            if ev and ev >= 1:
                # 记成 (卡 id, 状态)：既用于去重「给牌只给一次」，
                # 也用于统计「这个卡 id 有几张进化过」——随从死了也不清，
                # 换局才清（用户 2026-11-17：进化标记要一直留着）。
                prev_ev = old[1] if (old and isinstance(old, (tuple, list))) else 0
                st["ev"][key] = (cid, ev)
                for tid, cnt in on_evolve(cid, ev, prev_ev):
                    st["grant"][tid] = int(st["grant"].get(tid, 0)) + cnt

        return build()


    # ---------------- 关闭 ----------------
    def close(self):
        _OPEN[0] = None
        # 先放掉单实例锁，否则同一个进程里点「打开」会被自己挡住
        # （表现：手动关掉记牌器 → 控制台「打开」没反应，必须重启控制台）
        try:
            import singleton
            singleton.release("SvwbDeckTracker")
        except Exception:
            pass
        try:
            self.root.unbind_all("<MouseWheel>")
            self.root.unbind_all("<Button-4>")
            self.root.unbind_all("<Button-5>")
        except Exception:
            pass

        try:
            if self._after is not None:
                self.root.after_cancel(self._after)
        except Exception:
            pass
        # 界面自己的定时器（动画 tick / 卡图轮询 / 大图重试）也要停，否则
        # destroy 之后还会被 Tcl 调用，刷「invalid command name ..._tick」
        try:
            self.hud.stop_timers()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


def open(master=None):
    if _OPEN[0] is not None:
        try:
            _OPEN[0].root.lift()
            return _OPEN[0]
        except Exception:
            # 窗口已经被销毁但还挂着引用：把锁一起放掉，别把下次打开挡在门外
            try:
                import singleton
                singleton.release("SvwbDeckTracker")
            except Exception:
                pass
            _OPEN[0] = None
    # 跨进程单实例：记牌器同时只能有一个。
    # 走到这里本进程已经没有活着的窗口了，如果锁还在自己手里，说明上次不是
    # 正常关闭（异常 / 直接 destroy）—— 先放掉再抢，不然会一直「已经在运行」。
    try:
        import singleton
        if singleton.has("SvwbDeckTracker"):
            singleton.release("SvwbDeckTracker")
        if not singleton.acquire("SvwbDeckTracker"):
            print("记牌器已经在运行（另一个窗口或另一个助手实例）")
            return None
    except Exception:
        pass
    w = DeckTracker(master)
    _OPEN[0] = w
    return w

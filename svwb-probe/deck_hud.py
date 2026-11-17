# -*- coding: utf-8 -*-
"""记牌器渲染层：单个 Canvas 自绘，每行是持久图元。

对外接口只有 set_vm(vm) 和 resize(w, h)。
- 宽度 >= 520 时牌库自动排成双列，变宽就能多看一倍的牌；
- 右下角抓手可拖动改变窗口大小，双击抓手恢复高度自适应；
- 顶栏底部有一条红线，「我方 / 对方」两个标签挂在线上切换列表内容；
- 行位置用插值动画过渡，抽牌不销毁重建，所以不会闪。
"""
import time
import tkinter as tk
import tkinter.font as tkfont

try:
    import cardart                     # 卡图：本地缓存 + 后台下载
except Exception:
    cardart = None

# 界面版本号（2026-11-15）：窗口是无边框的，root.title 看不见，
# 所以版本要**画**在标题旁边 —— 远程排查时一眼能确认他跑的是哪一版。
try:
    from app_ver import VERSION as APP_VER
except Exception:
    APP_VER = ""

# ---------------------------------------------------------------- 调色板 ----
BG        = "#0c1015"
CARD      = "#171e27"
CARD_BD   = "#2a323d"
CARD_HI   = "#1c242e"
CARD_HI_BD = "#7a6236"
CARD_ZERO = "#12181f"
FOE_BG    = "#131a24"
FOE_BD    = "#222c3a"
GOLD      = "#e6c98d"
GOLD_D    = "#c9a86a"
GOLD_DD   = "#8f6f3c"
FG        = "#e7e1d3"
FG_FOE    = "#a9b7c6"
FG_ZERO   = "#6f7a86"
DIM       = "#8b95a2"
DIM2      = "#6b7683"
RULE      = "#232a33"
GREEN     = "#7fc08a"
RED       = "#cf8074"
TRACK     = "#1b222b"
TAB_RED   = "#d9433a"     # 「我方 / 对方」那条红线 / 选中态
EVO_SUPER_COLOR = "#ef7295"   # 超进化角标（和护符的玫红一个色系）
FOOT_FG   = "#65717f"     # 分区脚注（tips）：比正文暗、比分隔线亮，不抢视线

# 卡牌类型色（V1.0）：随从 / 法术 / 护符三类各一色，统一走暖色系。
# 色相隔开足够远（橙 20° / 金 44° / 玫红 345°），深色底上都能一眼认出；
# 对手栏用降亮度的同色，不跟我方的彩色抢视线。
KIND_COLOR = {"随从": "#f2854e", "法术": "#efc45c", "护符": "#ef7295"}
KIND_COLOR_FOE = {"随从": "#b2693f", "法术": "#ad8c45", "护符": "#a85a71"}

# 牌库排序（V1.0）：按钮挂在「牌库」分区头右侧，点开是两行单选浮窗。
# 浮窗画在 body 画布的世界坐标里，只是叠在行上面 —— 不占高度，牌库不会往下滑。
SORT_ASC, SORT_DESC = "asc", "desc"
SORT_TEXT = {SORT_ASC: "费用从低到高", SORT_DESC: "费用从高到低"}
SORT_SHORT = {SORT_ASC: "费用：低→高", SORT_DESC: "费用：高→低"}
POPUP_ITEM_H = 34       # 浮窗里每一行（一个选项）的高度
POPUP_PAD = 7           # 浮窗上下内边距
POPUP_BG = "#1b232e"
POPUP_HOT = "#27323f"

# ---------------------------------------------------------------- 尺寸 ------
HEAD_H   = 144          # 114 的顶栏 + 30 的切换条
TAB_H    = 30           # 顶栏底部「我方 / 对方」切换条
ROW_H    = 56           # 40 → 56：行高跟着行内卡图一起放大（卡图 48px）
ROW_GAP  = 4
SEC_HEAD = 34
SEC_GAP  = 10
FOOT_LH  = 22           # 分区脚注（tips）一行的高度
FOOT_GAP = 5            # 脚注与最后一行的间距
PAD_X    = 9
RADIUS   = 6
COL_GAP  = 8
GRIP     = 14
MIN_W    = 300
MIN_H    = 260
TWO_COL  = 520          # 宽度达到这个值，牌库改双列

# 行内卡图：贴在费用圆章左边、整行高的小卡面
ART_X    = 7            # 距行左边缘
ART_W    = 37           # 48 × (530/687)，官方原图 530×687 的等比宽度
ART_H    = 48
ART_GAP  = 8            # 与费用圆章之间的空隙
ART_OFF  = ART_W + ART_GAP      # 有卡图时，圆章和文字整体右移这么多
ART_Y    = (ROW_H - ART_H) / 2.0   # 卡图在行内垂直居中，别贴顶

# 悬停大图：必须和 cardart.SIZES 里的大图档位一致，否则永远取不到图
PREVIEW_PX = 380

# 头部纵向锚点
TITLE_Y  = 17
RATE_Y0, RATE_Y1 = 30, 66
RES_Y    = 88
BAR_Y    = 100

FAMILY = "Microsoft YaHei UI"


def _rr(cv, x1, y1, x2, y2, r, **kw):
    """圆角矩形：8 个控制点的 smooth 多边形，四角过渡自然。"""
    if x2 - x1 < 2 * r:
        r = max(1, (x2 - x1) / 2.0)
    if y2 - y1 < 2 * r:
        r = max(1, (y2 - y1) / 2.0)
    pts = (x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1)
    return cv.create_polygon(pts, smooth=True, splinesteps=16, **kw)


def _card(cv, x1, y1, x2, y2, r, fill, bd, tags):
    """圆角卡片：先铺一层边框色，再内缩 1px 铺填充 —— 圆角处的描边不会缺口。
    返回 (边框 id, 填充 id)，方便只改颜色不重画。"""
    a = _rr(cv, x1, y1, x2, y2, r, fill=bd, outline=bd, tags=tags)
    b = _rr(cv, x1 + 1, y1 + 1, x2 - 1, y2 - 1, max(1.0, r - 0.5),
            fill=fill, outline=fill, tags=tags)
    return a, b


# ------------------------------------------------------------ 状态说明 ------
# 2026-11-15 用户要求：状态栏出现异常时，最右边出现一个「？」，鼠标停留 0.3 秒
# 弹出「这是怎么回事 + 我该怎么办」；正常运行时不显示这个图标。
#
# 写法约定：一条 = (状态里出现的词, 标题, 正文)
#   · 只写用户自己能动手的事，不写内部实现；
#   · 正文用 \n 手动断行，每行控制在 22 个全角字以内（这样浮窗不会比
#     记牌器还宽，也就不需要 Label 自动折行 —— 自动折行会把「。」「①」
#     甩到单独的下一行，很难看）；
#   · 正常过程中的状态（正在连接游戏 / 对局中）不要放进这张表。
HELP_ROWS = (
    ("没找到游戏进程", "没认出游戏进程",
     "控制台扫了一遍正在运行的程序，没找到游戏。\n"
     "① 确认游戏已进到主界面，不是停在启动器；\n"
     "② 先开游戏或先开助手都行，每 12 秒自动重试；\n"
     "③ 一直不行：双击「诊断游戏进程.bat」，\n"
     "   把 logs\\进程诊断.txt 发回给作者。"),
    ("读取已断", "读取断开了",
     "之前是连上的，现在读不到数据 —— 多半是游戏重开\n"
     "或模拟器换了进程。\n"
     "① 把游戏退到桌面再进一次，助手会自动重连；\n"
     "② 还不行就重启助手；\n"
     "③ 反复出现：发 logs\\frida-out.txt 给作者。"),
    ("读取启动失败", "读取启动失败",
     "连上游戏了，但注入出错，通常是安全软件拦了。\n"
     "① 把助手加入杀软白名单，或临时关掉试一次；\n"
     "② 右键「启动.bat」→ 以管理员身份运行；\n"
     "③ 仍失败：发 logs\\frida_live.log 给作者。"),
    ("游戏还没进页面", "游戏还没加载完",
     "进程已经连上，但游戏主界面还没起来。\n"
     "进到对局后会自动开始读，这一条不用处理；\n"
     "如果一直停在这里，重启一次游戏即可。"),
    ("读取未启动", "实时读取没启动",
     "读取器根本没起来，所以没有任何数据。\n"
     "① 确认控制台还在：托盘里要有图标，\n"
     "   别只剩记牌器窗口；\n"
     "② 页签要在「影之诗」，实时读取开关要开；\n"
     "③ 最省事：退出助手后重新双击「启动.bat」。"),
    ("读取已停", "实时读取已停止",
     "对局结束后读取会暂停，开下一局会自动恢复。\n"
     "一直不恢复：重启助手；\n"
     "反复出现：发 logs\\frida_live.log 给作者。"),
)


def help_for(text):
    """状态文字 → (关键词, 标题, 正文)；正常状态返回 None。"""
    s = str(text or "")
    if not s:
        return None
    for key, title, body in HELP_ROWS:
        if key and key in s:
            return key, title, body
    return None


# 每条说明前面统一加的一句（2026-11-15 用户要求）：权限不一致是「连不上」
# 最常见、也最容易被忽略的原因，放在最前面让人先自己排掉。
ADMIN_HINT = "先检查助手是否以管理员权限打开。"


class DeckHud:
    """自绘记牌器。宿主只需给一个 Toplevel 和几个回调。"""

    def __init__(self, root, width=360, height=0, on_close=None, on_ai=None,
                 on_reset=None, on_resize=None, max_body=640, on_flip=None,
                 art_get=None, art_has=None):
        self.root = root
        self.w = int(max(MIN_W, width))
        self.fixed_h = int(height or 0)     # >0 表示用户拖过高度，不再自适应
        self.on_close = on_close
        self.on_ai = on_ai
        self.on_reset = on_reset
        self.on_flip = on_flip
        self.on_resize = on_resize
        self.max_body = max(120, max_body)
        if self.fixed_h:
            self.max_body = max(120, self.fixed_h - HEAD_H - 4)

        # 字号比 1.0 整体大四号（像素制，-N 就是 N px）
        # f_small / f_num / f_title / f_rate / f_tab 归顶栏，顶栏高度固定，不动；
        # 列表行跟着 ROW_H 放大，单独用 f_body / f_row_small / f_sec。
        self.f_title = tkfont.Font(family=FAMILY, size=-19, weight="bold")
        self.f_rate = tkfont.Font(family=FAMILY, size=-18, weight="bold")
        self.f_body = tkfont.Font(family=FAMILY, size=-18)
        self.f_bold = tkfont.Font(family=FAMILY, size=-18, weight="bold")
        self.f_small = tkfont.Font(family=FAMILY, size=-14)
        self.f_foot = tkfont.Font(family=FAMILY, size=-15)
        self.f_cost = tkfont.Font(family=FAMILY, size=-19, weight="bold")
        self.f_num = tkfont.Font(family=FAMILY, size=-15, weight="bold")
        self.f_tab = tkfont.Font(family=FAMILY, size=-14, weight="bold")
        self.f_row_small = tkfont.Font(family=FAMILY, size=-16)   # 行内：数量、类型
        self.f_sec = tkfont.Font(family=FAMILY, size=-16)         # 分区头

        self.head = tk.Canvas(root, width=self.w, height=HEAD_H, bg=BG,
                              bd=0, highlightthickness=0)
        self.head.pack(side="top", fill="x")
        self.body = tk.Canvas(root, width=self.w, bg=BG, bd=0,
                              highlightthickness=0)
        self.body.pack(side="top", fill="both", expand=True)

        self.items = {}          # key -> dict（行 / 分区头）
        self.secs = []           # [dict] 有序分区
        self.vm = {}
        self._collapsed = {}
        self._head_sig = None
        self._sec_sig = None
        self._content_h = 0.0
        self._last_h = 0
        self._anim = False
        self._silent = False
        self._scroll_cur = 0.0
        self._scroll_tgt = 0.0
        self._drag = None
        self._drag_block = 0.0
        self._reset_ask = 0.0     # 重置按钮的二次确认状态
        self._meas = {}
        self._hover = None
        self.tab = "self"         # self=我方（牌库/手牌），foe=对方（对手已见牌）
        # 牌库排序：默认「费用从低到高」；浮窗是画布上的图元，不参与布局
        self.sort_mode = SORT_ASC
        self._sort_btn = None     # 排序按钮的矩形（世界坐标）
        self._popup_open = False
        self._popup_rects = {}
        self._popup_bg = {}
        self._popup_hot = None

        # 状态栏「？」：异常才出现，悬停 0.3 秒弹说明（见 HELP_ROWS）
        self._help_key = None
        self._help_after = None
        self._hp = None
        self._hp_lbl = None

        # 卡图：art_get(cid, px) -> 图片路径或 None（没有就先空着，后台在下）
        self.art_get = art_get
        self.art_has = art_has
        self.art = bool(art_get)  # 当前这一帧要不要画卡图（由 vm["art"] 控制）
        self._imgs = {}           # 图片路径 -> PhotoImage（留着引用，否则会被回收）
        self._art_timer = None
        self._art_tries = 0
        self._pv = None           # 悬停大图的 Toplevel
        self._pv_lbl = None
        self._pv_after = None
        self._pv_row = None
        self._pv_tries = 0
        self._alive = True         # 关窗时置 False，动画 tick 不再续命
        self.body.bind("<Motion>", self._on_motion, add="+")
        self.body.bind("<Leave>", lambda e: self._hide_preview(), add="+")
        # 点空白处收起排序浮窗（点在浮窗自己身上时由浮窗的 tag 处理，这里放行）
        self.body.bind("<Button-1>", self._body_click, add="+")

        # 右下角缩放手柄
        self.grip = tk.Canvas(root, width=GRIP, height=GRIP, bg=BG, bd=0,
                              highlightthickness=0, cursor="size_nw_se")
        self._paint_grip()
        self.grip.place(relx=1.0, rely=1.0, anchor="se", x=-1, y=-1)
        self.grip.bind("<Button-1>", self._grip_press)
        self.grip.bind("<B1-Motion>", self._grip_move)
        self.grip.bind("<ButtonRelease-1>", self._grip_release)
        self.grip.bind("<Double-Button-1>", self._grip_auto)
        self.grip.bind("<MouseWheel>", self._wheel)
        self._rs = None
        self._rs_size = None

        # 只有顶栏能拖动窗口：列表区留给点击/折叠，免得点一张牌就把窗口带跑
        self.head.bind("<Button-1>", self._press)
        self.head.bind("<B1-Motion>", self._dragmove)
        for w in (self.head, self.body):
            w.bind("<MouseWheel>", self._wheel)
        self.body.bind("<Configure>", self._on_body_configure)

        # 恢复上次的尺寸（固定高度时不再随内容伸缩）
        try:
            self.root.geometry("%dx%d" % (self.w, self.fixed_h or 700))
        except Exception:
            pass

    # ------------------------------------------------------------ 尺寸 ----
    def _set_geom(self, w, h):
        """改尺寸时把**当前位置显式带上**。

        只写 "WxH" 时位置由 Tk 记着的 wmPtr->x/y 决定；窗口一旦被系统挪过
        （或换 DPI、被别的工具挪），那份值就过期，下一次改高度会把窗口
        拽回旧坐标 —— 用户 2026-11-11 反馈的「快速切我方/对方时位置乱跳」
        就是这条路。这里每次都读一次真实位置再一起写回去。
        """
        try:
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
        except Exception:
            x = y = None
        try:
            if x is None or x < 0 or y < 0:
                # Tk 的 geometry 把 "-x" 当成「距右/距底」，负坐标会直接飞掉，
                # 这种情况退回只给尺寸（保持原位置）。
                self.root.geometry("%dx%d" % (w, h))
            else:
                self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        except Exception:
            pass

    # ------------------------------------------------------------ 公共 ----
    def set_vm(self, vm):
        self.vm = vm or {}
        # 卡图开关：从「开」变「关」（或反过来）要把行全部重画 —— 左边多出来的
        # 那 32px 会改掉费用圆章和卡名的位置。
        art = bool(self.vm.get("art", True)) and bool(self.art_get)
        if art != self.art:
            self.art = art
            for k in list(self.items):
                self._kill_row(self.items.pop(k))
            self._imgs.clear()
            self._hide_preview()
        self._build_sections(self.vm)
        self._layout()
        self._sync_items()
        self._art_kick()
        self._paint_sections()
        self._paint_head()
        self._apply_height()
        self._apply_scrollregion()
        self._paint_scrollbar()
        self._start_anim()

    def resize(self, w, h, commit=True):
        """改窗口尺寸并重排。h=0 表示高度回到自适应。"""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = int(max(MIN_W, min(w, max(MIN_W, sw - 20))))
        if h:
            h = int(max(MIN_H, min(h, max(MIN_H, sh - 60))))
        if w == self.w and h == self.fixed_h:
            return
        self._close_popup()          # 宽度变了按钮会挪，浮窗跟着作废
        self.w = w
        self.fixed_h = h
        if h:
            self.max_body = max(120, h - HEAD_H - 4)
        else:
            # 回到自适应：内容上限重新按屏幕算，否则会沿用上次拖出来的高度
            self.max_body = max(340, min(620,
                                         int(self.root.winfo_screenheight()) - 280))
        self._last_h = 0
        self._head_sig = None
        self._sec_sig = None
        self._set_geom(w, h or self._auto_h())
        # 宽度变了，字体截断和列数都变，行必须按新宽度重画
        self._silent = True
        try:
            for k in list(self.items):
                self._kill_row(self.items.pop(k))
            self._build_sections(self.vm)
            self._layout()
            self._sync_items()
            self._paint_sections()
            self._paint_head()
            self._apply_height()
            self._apply_scrollregion()
            self._paint_scrollbar()
        finally:
            self._silent = False
        self._start_anim()
        if commit and self.on_resize:
            try:
                self.on_resize(w, h)
            except Exception:
                pass

    def destroy(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------ 分区 ----
    def _build_sections(self, vm):
        """vm["sections"] = [{key,title,note,side,items:[{cid,name,cost,kind,
        qty,playable,foe,zero}], empty}]；顺序即显示顺序。

        side 决定这条分区挂在哪个标签下（self=我方 / foe=对方）。
        不在当前标签的分区照样保留在 self.secs 里，只是不建行、不占高度 ——
        计数逻辑在 deck_tracker 里跑，跟显示无关，切回来数字是对的。
        """
        self.secs = []
        for s in vm.get("sections") or []:
            key = s.get("key") or "deck"
            folded = bool(self._collapsed.get(key))
            side = s.get("side") or ("foe" if key == "foe" else "self")
            hidden = side != self.tab
            sec = {"key": key, "title": s.get("title") or "",
                   "note": s.get("note") or "",
                   "collapsed": folded,
                   "foldable": bool(s.get("foldable")),
                   "tone": s.get("tone") or "main",
                   "side": side, "hidden": hidden,
                   "rows": [], "y": 0.0, "h": 0.0, "count": 0,
                   "empty": s.get("empty") or "",
                   "foot": s.get("foot") or "",
                   "foot_lines": [], "foot_y": 0.0}
            items = self._sort_items(key, s.get("items") or [])
            sec["count"] = len(items)
            for it in items:
                # 折叠的分区不建行：否则行会留在旧坐标上压住下面的分区
                if folded or hidden:
                    continue
                # 行键带上进化状态：同名的两张牌一张进化一张没进化时，
                # 数据层会拆成两行，键里不带 ev 就会互相覆盖（2026-11-17）。
                k = "%s:%s:%s" % (key, it.get("cid"), it.get("ev") or 0)
                row = self.items.get(k)
                if row is None:
                    row = {"key": k, "sec": key, "kind": "row",
                           "cx": None, "cy": None, "tx": 0.0, "ty": 0.0,
                           "x0": PAD_X, "rw": self.w - PAD_X * 2,
                           "ids": {}, "qty": None, "playable": None,
                           "zero": None, "ev": None}
                    self.items[k] = row
                row["data"] = it
                # 名字 / 类型变了要重画这一行：行对象是按 `分区:卡id` 复用的，
                # 只刷新数量的话，「获得 · 推测」那行等它真被打出来后，
                # 类型栏会一直留着「推测·随从」（用户 2026-11-11 反馈前先堵上）。
                sig = (it.get("cid"), it.get("name"), it.get("kind"),
                       bool(it.get("grant")), it.get("ev") or 0)
                if row.get("sig") != sig:
                    if row.get("ids"):
                        self._kill_row(row)
                    row["sig"] = sig
                sec["rows"].append(row)
            self.secs.append(sec)

    def _sort_items(self, key, items):
        """牌库按费用重排（V1.0）：只在显示层动，不改 deck_tracker 的数据。

        同一费用的部分保持 deck_tracker 给的顺序（职业 → 稀有度 → 名字）：
        整表 reverse 会连同一费用的牌也倒过来，看着像被打乱了。
        cost 缺失的牌（卡表没查到的）两种排序都压到最后。
        """
        if key != "deck" or len(items) < 2:
            return items
        sign = 1 if self.sort_mode == SORT_ASC else -1

        def ck(it):
            try:
                return (0, sign * int(it.get("cost")))
            except (TypeError, ValueError):
                return (1, 0)

        return sorted(items, key=ck)      # sorted 是稳定排序，同费用不乱序

    def _sec_cols(self, sec):
        """牌库够宽就双列：变宽 = 多看一倍的牌。手牌/对手保持单列更好读。"""
        if sec["key"] == "deck" and self.w >= TWO_COL:
            return 2
        return 1

    def _layout(self):
        """重算每一行的目标 x/y（世界坐标，不含滚动）。"""
        y = 4.0
        for sec in self.secs:
            if sec["hidden"]:
                # 不在当前标签下：不占高度，但要有个 y，免得序列化/断言读到 0
                sec["y"] = y
                sec["h"] = 0.0
                continue
            sec["y"] = y
            y += SEC_HEAD
            cols = self._sec_cols(sec)
            inner = self.w - PAD_X * 2
            cw = (inner - COL_GAP * (cols - 1)) / float(cols)
            rows = sec["rows"]
            if sec["collapsed"]:
                sec["h"] = SEC_HEAD
                sec["foot_lines"] = []
                y += SEC_GAP
                continue
            n = len(rows)
            lines = max(1, (n + cols - 1) // cols) if n else 1
            for i, row in enumerate(rows):
                # 竖向填充：先填满左列再填右列，费用顺序从上往下读
                c = i // lines if cols > 1 else 0
                r = i % lines
                row["x0"] = PAD_X + c * (cw + COL_GAP)
                row["rw"] = cw
                row["ty"] = y + r * (ROW_H + ROW_GAP)
            if n:
                base = SEC_HEAD + lines * (ROW_H + ROW_GAP)
            else:
                base = SEC_HEAD + 30          # 空分区占一行提示
            # 分区脚注（tips）：按可用宽度折行后再算高度，窄窗口自动变两行
            fl = (self._wrap(sec["foot"], self.f_foot, self.w - PAD_X * 2 - 12)
                  if sec["foot"] else [])
            fh = (FOOT_GAP + len(fl) * FOOT_LH) if fl else 0.0
            sec["foot_lines"] = fl
            sec["foot_y"] = sec["y"] + base
            sec["h"] = base + fh
            y = sec["y"] + sec["h"] + SEC_GAP
        self._content_h = max(0.0, y - SEC_GAP + 4)

    # ------------------------------------------------------------ 行同步 ---
    def _sync_items(self):
        alive = set()
        for sec in self.secs:
            for row in sec["rows"]:
                alive.add(row["key"])
                if not row["ids"]:
                    self._draw_row(row)
                else:
                    self._refresh_row(row)
        for k in list(self.items):
            if k not in alive:
                self._kill_row(self.items.pop(k))
        self._raise_popup()

    def _kill_row(self, row):
        for i in row.get("ids", {}).values():
            try:
                self.body.delete(i)
            except Exception:
                pass
        row["ids"] = {}

    # ------------------------------------------------------------ 卡图 ---
    def _img(self, path):
        """同一张图只读一次盘：PhotoImage 必须留引用，否则被回收就变成空白。"""
        im = self._imgs.get(path)
        if im is not None:
            return im or None
        try:
            im = tk.PhotoImage(file=path)
        except Exception:
            im = False
        self._imgs[path] = im
        return im or None

    def _art_slot(self, cid):
        """这一行要不要留卡图的位置：开关开着、并且官方卡表认得这张卡。"""
        if not self.art or not self.art_get:
            return False
        if self.art_has is None:
            return True
        try:
            return bool(self.art_has(cid))
        except Exception:
            return False

    def _art_path(self, cid):
        if not self.art or not self.art_get or cid is None:
            return None
        try:
            return self.art_get(cid, ART_H)
        except Exception:
            return None

    def _art_try(self, row, placed=False):
        """给一行补上卡图。placed=True 表示这行已经摆到位（补图时要跟上位移）。"""
        if not self.art or "art" in row.get("ids", {}):
            return bool(row.get("ids", {}).get("art"))
        p = self._art_path(row["data"].get("cid"))
        if not p:
            return False
        im = self._img(p)
        if not im:
            return False
        try:
            i = self.body.create_image(row["x0"] + ART_X, ART_Y, image=im,
                                       anchor="nw", tags=(row["key"], "row"))
        except Exception:
            return False
        row["ids"]["art"] = i
        if placed:
            try:
                self.body.move(i, row.get("cx") or 0.0, row.get("cy") or 0.0)
            except Exception:
                pass
        return True

    def _art_kick(self):
        """把已经下好的卡图挂上去；还没好的过一会儿再看（不阻塞界面）。"""
        if not self.art or not self.art_get:
            return
        waiting = 0
        for sec in self.secs:
            if sec.get("hidden") or sec.get("collapsed"):
                continue
            for row in sec["rows"]:
                if row["ids"].get("art"):
                    continue
                if not self._art_slot(row["data"].get("cid")):
                    continue
                if self._art_try(row, placed=True):
                    continue
                waiting += 1
        if waiting:
            self._art_schedule()
        else:
            self._art_tries = 0

    def _art_schedule(self):
        if self._art_timer is not None:
            return
        self._art_tries = getattr(self, "_art_tries", 0) + 1
        if self._art_tries > 60:          # 约 30 秒还没齐：别再空转
            return
        try:
            self._art_timer = self.root.after(500, self._art_poll)
        except Exception:
            self._art_timer = None

    def _art_poll(self):
        self._art_timer = None
        self._art_kick()

    # -------------------------------------------------- 悬停看大图 ---
    def _row_at(self, x, y):
        try:
            items = self.body.find_overlapping(x - 1, y - 1, x + 1, y + 1)
        except Exception:
            return None
        for i in reversed(items):
            try:
                tags = self.body.gettags(i)
            except Exception:
                continue
            for t in tags:
                row = self.items.get(t)
                if row and row.get("kind") == "row":
                    return row
        return None

    def _on_motion(self, ev):
        """鼠标停在某一行的**左侧卡图区**上 → 300ms 后弹大卡图。

        热区只到名字起点为止（卡图 + 费用圆章）：整行都触发的话，鼠标顺着
        名单往下扫也会一路弹图，很吵。想看图就停在卡面上，看名字不会被打扰。
        """
        if self._popup_open:
            # 浮窗压着行：这时候不做悬停预览，只更新浮窗里那两行的悬停色
            self._popup_hover(ev)
            self._hide_preview()
            return
        if not self.art or not self.art_get:
            return
        try:
            mx = self.body.canvasx(ev.x)
            row = self._row_at(mx, self.body.canvasy(ev.y))
        except Exception:
            return
        if row is not None:
            hw = row.get("hot_w")
            if hw:
                dx = row.get("cx") or 0.0
                x0 = row["x0"] + dx
                if not (x0 <= mx <= x0 + hw):
                    row = None
        if row is self._pv_row:
            return
        self._pv_row = row
        self._cancel_pv()
        if row is None:
            self._hide_preview()
            return
        # 图在不在都不影响先挂定时器：图还没到就进 _show_preview 里等，别直接放弃
        self._pv_tries = 0
        self._pv_after = self.root.after(
            300, lambda r=row: self._show_preview(r))

    def _cancel_pv(self):
        if self._pv_after is not None:
            try:
                self.root.after_cancel(self._pv_after)
            except Exception:
                pass
            self._pv_after = None

    def _preview_path(self, row):
        """取这一行的大图。取不到返回 None（可能是没缓存，也可能还在下）。"""
        cid = row["data"].get("cid")
        if cid is None or not self.art_get:
            return None
        try:
            return self.art_get(cid, PREVIEW_PX)
        except Exception:
            return None

    def _show_preview(self, row):
        self._pv_after = None
        if self._pv_row is not row:
            return
        path = self._preview_path(row)
        if not path:
            # 图还没下完：隔 400ms 再看几次（最多约 3 秒），别让悬停白等
            self._pv_tries = getattr(self, "_pv_tries", 0) + 1
            if self._pv_tries <= 8:
                try:
                    self._pv_after = self.root.after(
                        400, lambda r=row: self._show_preview(r))
                except Exception:
                    self._pv_after = None
            return
        im = self._img(path)
        if not im:
            return
        try:
            if self._pv is None:
                self._pv = tk.Toplevel(self.root)
                self._pv.overrideredirect(True)
                self._pv.attributes("-topmost", True)
                self._pv_lbl = tk.Label(self._pv, bd=0, bg=BG,
                                        highlightthickness=1,
                                        highlightbackground="#3a4553")
                self._pv_lbl.pack()
            self._pv_lbl.configure(image=im)
            w, h = im.width(), im.height()
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw = self.root.winfo_width()
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            x = rx + rw + 8                 # 默认贴右侧，不挡列表
            if x + w + 4 > sw:
                x = rx - w - 10             # 右边放不下就翻到左边
            if x < 4:
                x = max(4, sw - w - 8)
            y = ry + 60
            if y + h + 4 > sh:
                y = max(4, sh - h - 8)
            self._pv.geometry("%dx%d+%d+%d" % (w + 2, h + 2, int(x), int(y)))
            self._pv.deiconify()
        except Exception:
            self._hide_preview()

    def _hide_preview(self):
        self._cancel_pv()
        self._pv_row = None
        if self._pv is not None:
            try:
                self._pv.withdraw()
            except Exception:
                pass
        self._pv_tries = 0

    # ---------------------------------------------------- 状态栏「？」 ---
    def _paint_help(self, cv, ry, hint):
        """在状态行最右边画「？」（只有异常状态才画）。返回是否画了。

        正常运行时这个图标不存在 —— 用户 2026-11-15 的要求，避免平时多一个
        没用的符号，出问题时又能一眼看到「这里能问」。
        """
        if not hint:
            self._help_key = None
            self._help_hide()
            return False
        self._help_key = hint[0]
        cx = self.w - 13.0
        tags = ("btn_help",)
        _rr(cv, cx - 7.5, ry - 7.5, cx + 7.5, ry + 7.5, 7.5,
            fill="#221d16", outline="#5a4a30", width=1, tags=tags)
        cv.create_text(cx, ry + 0.5, text="?", anchor="center", fill=GOLD_D,
                       font=self.f_small, tags=tags)
        cv.tag_bind("btn_help", "<Enter>", lambda e: self._help_hover(True))
        cv.tag_bind("btn_help", "<Leave>", lambda e: self._help_hover(False))
        return True

    def _help_hover(self, on):
        """悬停 0.3 秒才弹（和卡图大图一个节奏），移开立刻收 —— 不做常驻。"""
        self._cancel_help()
        if not on:
            self._help_hide()
            return
        if not getattr(self, "_help_key", None):
            return
        try:
            self._help_after = self.root.after(300, self._help_show)
        except Exception:
            self._help_after = None

    def _cancel_help(self):
        tid = getattr(self, "_help_after", None)
        if tid:
            try:
                self.root.after_cancel(tid)
            except Exception:
                pass
            self._help_after = None

    def _help_show(self):
        self._help_after = None
        key = getattr(self, "_help_key", None)
        if not key:
            return
        hint = next((r for r in HELP_ROWS if r[0] == key), None)
        if not hint:
            return
        _k, title, body = hint
        try:
            if self._hp is None:
                self._hp = tk.Toplevel(self.root)
                self._hp.overrideredirect(True)
                self._hp.attributes("-topmost", True)
                self._hp_lbl = tk.Label(
                    self._hp, bd=0, bg="#12171e", fg=FG, justify="left",
                    anchor="nw", font=self.f_small, padx=11, pady=9,
                    highlightthickness=1,
                    highlightbackground="#3a4553")
                self._hp_lbl.pack()
            self._hp_lbl.configure(text="【%s】\n%s\n%s"
                                        % (title, ADMIN_HINT, body))
            self._hp_lbl.update_idletasks()
            pw = max(200, self._hp_lbl.winfo_reqwidth()) + 2
            ph = self._hp_lbl.winfo_reqheight() + 2
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            sw, sh = (self.root.winfo_screenwidth(),
                      self.root.winfo_screenheight())
            x = rx + self.w - pw - 6          # 右对齐状态行
            x = max(4, min(x, sw - pw - 4))   # 贴到屏幕边上就收回来
            y = ry + RES_Y - ph - 12          # 默认弹在状态行上方
            if y < 4:
                y = ry + RES_Y + 16           # 上面放不下就往下弹
            if y + ph + 4 > sh:
                y = max(4, sh - ph - 8)
            self._hp.geometry("%dx%d+%d+%d" % (pw, ph, int(x), int(y)))
            self._hp.deiconify()
        except Exception:
            self._help_hide()

    def _help_hide(self):
        self._cancel_help()
        if self._hp is not None:
            try:
                self._hp.withdraw()
            except Exception:
                pass

    # -------------------------------------------------- 牌库排序浮窗 ---
    def _sort_button(self, sec, ty):
        """「牌库」分区头右侧的排序按钮：显示当前排序方式，点开是两行单选。

        画在 body 画布上（世界坐标），跟分区头一起重画；按钮不挂 fold 的 tag，
        否则点排序会顺手把牌库折叠掉。
        """
        cv = self.body
        lab = SORT_SHORT.get(self.sort_mode, SORT_SHORT[SORT_ASC])
        chev = "▾"
        tw = self.f_sec.measure(lab)
        cw = self.f_sec.measure(chev)
        bh = 20.0
        x2 = self.w - PAD_X - cw - 13       # 右边给折叠箭头留位
        x1 = x2 - (tw + cw + 26)            # 文字与箭头之间留 8px，别粘在一起
        if x1 < PAD_X + 62:                 # 太窄就别硬塞，宁可不出这个按钮
            self._sort_btn = None
            return
        on = self._popup_open
        tags = ("sec", "btn_sort")
        _rr(cv, x1, ty - bh / 2.0, x2, ty + bh / 2.0, bh / 2.0,
            fill=("#25313f" if on else "#1a2230"),
            outline=(GOLD_DD if on else "#39465a"), width=1, tags=tags)
        cv.create_text(x1 + 9, ty + 0.5, text=lab, anchor="w",
                       fill=(GOLD if on else DIM), font=self.f_sec, tags=tags)
        cv.create_text(x2 - 9, ty + 0.5, text=chev, anchor="e",
                       fill=(GOLD_D if on else DIM2), font=self.f_sec, tags=tags)
        self._sort_btn = (x1, ty - bh / 2.0, x2, ty + bh / 2.0)
        cv.tag_bind("btn_sort", "<Button-1>",
                    lambda e: self._toggle_popup())

    def _toggle_popup(self):
        self._drag_block = time.time() + 0.3
        if self._popup_open:
            self._close_popup()
            self._sec_sig = None
            self._paint_sections()
        else:
            self._sec_sig = None            # 按钮换成按下态
            self._paint_sections()
            self._paint_popup()

    def _paint_popup(self):
        """两行单选浮窗。纯画布图元、世界坐标：叠在行上面，不占高度。"""
        if not self._sort_btn:
            return
        cv = self.body
        cv.delete("popup")
        lab_w = max(self.f_sec.measure(t) for t in SORT_TEXT.values())
        pw = lab_w + 26 + 20
        ph = POPUP_PAD * 2 + POPUP_ITEM_H * 2
        bx1, by1, bx2, by2 = self._sort_btn
        x2 = min(self.w - PAD_X, bx2)
        x1 = x2 - pw
        if x1 < PAD_X:
            x1, x2 = PAD_X, PAD_X + pw
        y1 = by2 + 5.0
        if y1 + ph > self._content_h - 4:    # 下面放不下就往上翻
            y1 = max(2.0, by1 - 5.0 - ph)
        y2 = y1 + ph
        # 投影：深色底上垫一层更黑的偏移块，浮窗才「浮」得起来
        _rr(cv, x1 + 2, y1 + 3, x2 + 2, y2 + 3, 9, fill="#070a0e",
            outline="#070a0e", tags=("popup",))
        _rr(cv, x1, y1, x2, y2, 9, fill=POPUP_BG, outline="#43515f",
            width=1, tags=("popup",))
        self._popup_rects = {}
        self._popup_bg = {}
        for i, mode in enumerate((SORT_ASC, SORT_DESC)):
            iy0 = y1 + POPUP_PAD + i * POPUP_ITEM_H
            iy1 = iy0 + POPUP_ITEM_H
            cy = (iy0 + iy1) / 2.0
            on = self.sort_mode == mode
            tag = "popup_" + mode
            self._popup_bg[mode] = _rr(cv, x1 + 4, iy0, x2 - 4, iy1, 6,
                                       fill=POPUP_BG, outline=POPUP_BG,
                                       tags=("popup", tag))
            if i == 0:                      # 两行之间一条发丝线
                cv.create_line(x1 + 14, iy1, x2 - 14, iy1, fill="#2a3542",
                               tags=("popup",))
            ccx = x1 + 18
            ring = GOLD if on else "#7d8794"
            cv.create_oval(ccx - 5.6, cy - 5.6, ccx + 5.6, cy + 5.6,
                           fill="", outline=ring, width=1.4,
                           tags=("popup", tag))
            if on:                          # 已选：实心圆
                cv.create_oval(ccx - 2.7, cy - 2.7, ccx + 2.7, cy + 2.7,
                               fill=GOLD, outline=GOLD, tags=("popup", tag))
            cv.create_text(x1 + 30, cy + 0.5, text=SORT_TEXT[mode], anchor="w",
                           fill=(GOLD if on else "#c8d0da"), font=self.f_sec,
                           tags=("popup", tag))
            cv.tag_bind(tag, "<Button-1>",
                        lambda e, m=mode: self._pick_sort(m))
            self._popup_rects[mode] = (x1, iy0, x2, iy1)
        self._popup_open = True
        self._popup_hot = None
        self._raise_popup()

    def _raise_popup(self):
        if not self._popup_open:
            return
        try:
            self.body.tag_raise("popup")
        except Exception:
            pass

    def _popup_hit(self, x, y):
        for mode, (x1, y1, x2, y2) in self._popup_rects.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                return mode
        return None

    def _popup_hover(self, ev):
        try:
            cy = self.body.canvasy(ev.y)
        except Exception:
            return
        mode = self._popup_hit(ev.x, cy)
        if mode == self._popup_hot:
            return
        self._popup_hot = mode
        for m, tid in self._popup_bg.items():
            try:
                col = POPUP_HOT if m == mode else POPUP_BG
                self.body.itemconfigure(tid, fill=col, outline=col)
            except Exception:
                pass

    def _close_popup(self):
        self._popup_open = False
        self._popup_rects = {}
        self._popup_bg = {}
        self._popup_hot = None
        try:
            self.body.delete("popup")
        except Exception:
            pass

    def _body_click(self, ev):
        """点浮窗以外的地方就把浮窗收起来（点在按钮上交给按钮自己 toggle）。"""
        if not self._popup_open:
            return
        try:
            cy = self.body.canvasy(ev.y)
        except Exception:
            cy = ev.y
        if self._popup_hit(ev.x, cy):
            return
        b = self._sort_btn
        if b and b[0] <= ev.x <= b[2] and b[1] <= cy <= b[3]:
            return
        self._close_popup()

    def _pick_sort(self, mode):
        """选中一项：换排序方式 → 关浮窗 → 重排（行会插值滑到新位置）。"""
        if mode not in (SORT_ASC, SORT_DESC):
            return
        changed = mode != self.sort_mode
        self.sort_mode = mode
        self._close_popup()
        self._sec_sig = None
        if changed:
            self._scroll_cur = self._scroll_tgt = 0.0
            try:
                self.body.yview_moveto(0)
            except Exception:
                pass
            self.set_vm(self.vm)
        else:
            self._paint_sections()


    def _draw_row(self, row):
        it = row["data"]
        cv = self.body
        k = row["key"]
        tags = (k, "row")
        x1 = row["x0"]
        rw = row["rw"]
        x2 = x1 + rw
        y0, y1 = 0.0, ROW_H
        foe = bool(it.get("foe"))
        playable = bool(it.get("playable"))
        zero = bool(it.get("zero"))

        fill = FOE_BG if foe else (CARD_HI if playable else
                                   (CARD_ZERO if zero else CARD))
        bd = FOE_BD if foe else (CARD_HI_BD if playable else CARD_BD)
        ids = row["ids"]
        ids["bg2"], ids["bg"] = _card(cv, x1, y0, x2, y1, RADIUS, fill, bd, tags)

        # 左色条：只表示职业。可打出时不再把它染成金色 —— 职业色是辨认
        # 「这是什么职业的牌」的锚点，一变色就认不出了（用户 2026-11-08）。
        # 「能打出」改由整行金边 + 费用圆章 + 费用数字一起讲，信息不丢。
        bar = None
        if foe:
            bar = "#3c4a5c"
        elif it.get("cls_color"):
            bar = it["cls_color"]
        if bar:
            ids["bar"] = _rr(cv, x1 + 1.4, y0 + 9, x1 + 4.2, y1 - 9, 1.2,
                             fill=bar, outline=bar, tags=tags)

        # 卡图：有图（或图还在下）的卡固定留位，否则后面的元素会在图到货时
        # 突然整体右移一下；官方卡表里查不到的卡干脆不留位，免得留个黑洞。
        slot = self._art_slot(it.get("cid"))
        off = ART_OFF if slot else 0.0
        if slot:
            ids["artbg"] = _rr(cv, x1 + ART_X - 1.5, ART_Y - 1.5,
                               x1 + ART_X + ART_W + 1.5, ART_Y + ART_H + 1.5, 3.5,
                               fill="#0e141b",
                               outline=("#2b3746" if not foe else "#232f3d"),
                               width=1, tags=tags)

        # 费用圆章
        r = 16.0
        ccx, ccy = x1 + 9 + off + r, ROW_H / 2.0
        ids["ring"] = cv.create_oval(ccx - r, ccy - r, ccx + r, ccy + r,
                                     fill=("#161d26" if not foe else "#141b24"),
                                     outline=self._ring_color(it, playable, foe),
                                     width=1.3, tags=tags)
        ids["cost"] = cv.create_text(ccx, ccy + 0.5, text=str(it.get("cost", 0)),
                                     fill=self._cost_color(playable, foe),
                                     font=self.f_cost, tags=tags)

        nx = x1 + 9 + off + r * 2 + 11
        # 进化状态（2026-11-17）：不额外画图元 —— 画布每加一个 item，入场/重排
        # 的位移就要多照顾一个，之前试着画独立角标会把两行叠在一起。
        # 这里改成「类型栏后缀 + 变色」，用的还是原来那个 text，布局完全不变。
        ev = int(it.get("ev") or 0)
        # 悬停热区右边界 = 名字起点（卡图 + 费用圆章），存相对行左的宽度：
        # 行横向重排时这个值不变，不用跟着改
        row["hot_w"] = nx - x1
        name = it.get("name") or ""
        # 右侧：数量胶囊 → 类型 → 名字（名字最后画，按剩余宽度截断）
        qty = it.get("qty")
        qty = 1 if qty is None else int(qty)
        qh = 26.0
        qw = 36.0 if qty < 10 else 42.0
        qx2 = x2 - 8
        ids["qbg"] = _rr(cv, qx2 - qw, ROW_H / 2.0 - qh / 2.0, qx2,
                         ROW_H / 2.0 + qh / 2.0, 10,
                         fill=("#1e2631" if not foe else "#18202b"),
                         outline=("#303a46" if not foe else "#232c39"),
                         width=1, tags=tags)
        ids["qty"] = cv.create_text(qx2 - qw / 2.0, ROW_H / 2.0 + 0.5,
                                    text="×%d" % qty,
                                    fill=("#cbd3dc" if not foe else "#8f9dab"),
                                    font=self.f_row_small, tags=tags)

        kind = it.get("kind") or ""
        # 类型栏加进化后缀：随从 → 随从·进化 / 随从·超进化
        if ev > 0:
            kind = (kind + ("·超进化" if ev >= 2 else "·进化")) if kind else \
                   ("超进化" if ev >= 2 else "进化")
        kx = qx2 - qw - 9
        # 窄列（双列模式下）不画类型，否则名字只剩两三个字
        if kind and rw >= 268:
            kcol = ((KIND_COLOR_FOE if foe else KIND_COLOR).get(it.get("kind"))
                    or ("#64707e" if foe else DIM2))
            if ev > 0:      # 进化用金、超进化用玫红，和行内其它强调色一致
                kcol = EVO_SUPER_COLOR if ev >= 2 else GOLD_D
            ids["kind"] = cv.create_text(kx, ROW_H / 2.0 + 0.5, text=kind,
                                         anchor="e", fill=kcol,
                                         font=self.f_row_small, tags=tags)
            kx -= self.f_row_small.measure(kind) + 9

        avail = kx - nx
        ids["name"] = cv.create_text(nx, ROW_H / 2.0 + 0.5,
                                     text=self._clip(name, self.f_body, avail),
                                     anchor="w",
                                     fill=(FG_FOE if foe else
                                           (FG_ZERO if zero else
                                            (FG if not playable else "#f5efe1"))),
                                     font=self.f_body, tags=tags)
        row["qty"] = qty
        row["playable"] = playable
        row["zero"] = zero
        row["ev"] = ev
        if slot:
            self._art_try(row)
        # 入场：从右侧 14px 处滑入；重排（改窗口大小）时直接就位，不要动画
        row["tx"] = 0.0
        row["cy"] = row["ty"]
        if self._silent:
            row["cx"] = 0.0
            self._move_row(row, 0.0, row["ty"] - y0)
            self._snap_row(row)
        else:
            row["cx"] = 14.0
            self._move_row(row, 14.0, row["ty"] - y0)

    def _ring_color(self, it, playable, foe):
        """费用圆章的描边色。抽成方法是因为「能不能打出」会随 PP 变化，
        刷新时要和首绘用同一套判断，否则圆章永远停在画上去那一刻的颜色。

        2026-11-16 用户反馈：0/1 费的牌在牌库里也一直是金圈，看起来像「可打出」。
        以前这里有个 `cost <= 1` 就返回暗金的装饰分支（原意是让低费牌显眼），
        但「圆章变金」在记牌器里已经是「能打出」的语义，两者撞车了 ——
        现在只有真能打出才是金色，其余一律同一种冷灰。
        """
        if playable:
            return GOLD
        if foe:
            return "#46566a"
        return "#414d5b"

    def _cost_color(self, playable, foe):
        if foe:
            return FG_FOE
        return GOLD if playable else FG

    def _refresh_row(self, row):
        """已有行：只改会变的东西（数量 / 高亮 / 置灰 / 费用圆章）。"""
        it = row["data"]
        cv = self.body
        qty = it.get("qty")
        qty = 1 if qty is None else int(qty)
        if qty != row.get("qty"):
            try:
                cv.itemconfigure(row["ids"]["qty"], text="×%d" % qty)
            except Exception:
                pass
            row["qty"] = qty
        playable = bool(it.get("playable"))
        zero = bool(it.get("zero"))
        if playable != row.get("playable") or zero != row.get("zero"):
            foe = bool(it.get("foe"))
            fill = FOE_BG if foe else (CARD_HI if playable else
                                       (CARD_ZERO if zero else CARD))
            bd = FOE_BD if foe else (CARD_HI_BD if playable else CARD_BD)
            try:
                cv.itemconfigure(row["ids"]["bg2"], fill=bd, outline=bd)
                cv.itemconfigure(row["ids"]["bg"], fill=fill, outline=fill)
                cv.itemconfigure(row["ids"]["name"],
                                 fill=(FG_FOE if foe else (FG_ZERO if zero else
                                                           (FG if not playable else "#f5efe1"))))
                # 费用圆章也要跟着变金/变回普通色，否则 PP 一变，
                # 边框已经更新成可打出，圆章还停在旧颜色上（用户 2026-11-08）
                cv.itemconfigure(row["ids"]["ring"],
                                 outline=self._ring_color(it, playable, foe))
                cv.itemconfigure(row["ids"]["cost"],
                                 fill=self._cost_color(playable, foe))
            except Exception:
                pass
            row["playable"] = playable
            row["zero"] = zero

    # ------------------------------------------------------------ 分区头 ---
    def _paint_sections(self):
        """画所有分区头。签名没变就一个 item 都不动（避免 2Hz 闪）。"""
        shown = [s for s in self.secs if not s["hidden"]]
        sig = (self.w, self.tab) + tuple(
            (s["key"], s["title"], s["note"], s["collapsed"],
             s["count"], round(s["y"], 1), s["empty"], s["foot"],
             tuple(s.get("foot_lines") or ()), round(s["h"], 1)) for s in shown)
        if sig == self._sec_sig:
            return
        self._sec_sig = sig
        cv = self.body
        cv.delete("sec")
        self._sort_btn = None        # 重画分区头会重新给牌库按钮定位
        if not shown:
            # 对方标签下还没看到牌：给一句提示，别让窗口空成一片黑
            cv.create_text(self.w / 2.0, 36, anchor="center",
                           text=("还没有看到对手出牌" if self.tab == "foe"
                                 else "暂无数据"),
                           fill=DIM2, font=self.f_sec, tags=("sec",))
            return
        for sec in shown:
            self._sec_head(sec)
            if not sec["rows"] and not sec["collapsed"] and sec["empty"]:
                cv.create_text(PAD_X + 8, sec["y"] + SEC_HEAD + 12,
                               text=sec["empty"], anchor="w", fill=DIM2,
                               font=self.f_sec, tags=("sec",))
            # 分区脚注：说明这条分区统计的是什么，柔和色不抢视线
            for i, line in enumerate(sec.get("foot_lines") or []):
                cv.create_text(PAD_X + 8,
                               sec["foot_y"] + FOOT_GAP + i * FOOT_LH + FOOT_LH / 2.0,
                               text=line, anchor="w", fill=FOOT_FG,
                               font=self.f_foot, tags=("sec",))
        self._raise_popup()          # 分区头是后画的，浮窗要重新压回最上面

    def _sec_head(self, sec):
        cv = self.body
        tag = "hdr:" + sec["key"]
        y = sec["y"]
        tone_foe = sec["tone"] == "foe"
        mk = "#5d7288" if tone_foe else GOLD_D
        tags = (tag, "sec")
        if sec["collapsed"]:
            # 收起的分区给一条淡底，看着像个可展开的抽屉
            _rr(cv, PAD_X, y + 1, self.w - PAD_X, y + SEC_HEAD - 5, 4,
                fill="#10161e", outline="#232c37", width=1, tags=tags)
        _rr(cv, PAD_X + 1, y + 7, PAD_X + 3.4, y + 25, 1.2, fill=mk,
            outline=mk, tags=tags)
        tx = PAD_X + 11
        ty = y + 15.5
        cv.create_text(tx, ty, text=sec["title"], anchor="w",
                       fill=("#8b97a5" if tone_foe else DIM),
                       font=self.f_sec, tags=tags)
        tx += self.f_sec.measure(sec["title"]) + 8
        cv.create_text(tx, ty, text=sec["note"], anchor="w",
                       fill=DIM2, font=self.f_sec, tags=tags)
        if sec["key"] == "deck" and not sec["collapsed"]:
            self._sort_button(sec, ty)
        if sec["collapsed"]:
            cv.create_text(self.w - PAD_X - 4, ty,
                           text="▸ %d 张" % sec["count"], anchor="e",
                           fill=DIM2, font=self.f_sec, tags=tags)
        elif sec["foldable"]:
            cv.create_text(self.w - PAD_X - 4, ty, text="▾", anchor="e",
                           fill=DIM2, font=self.f_sec, tags=tags)
        if sec["foldable"]:
            key = sec["key"]
            cv.tag_bind(tag, "<Button-1>",
                        lambda e, k=key: self._toggle_section(k))

    def _toggle_section(self, key):
        self._drag_block = time.time() + 0.3
        self._close_popup()          # 折叠后按钮会挪走，浮窗留在原地很怪
        self._collapsed[key] = not self._collapsed.get(key)
        self._sec_sig = None
        self.set_vm(self.vm)

    # ------------------------------------------------------------ 滚动条 ---
    def _paint_scrollbar(self):
        """滚动条画在世界坐标里，所以每次按当前视口顶部偏移重画。"""
        cv = self.body
        cv.delete("sbar")
        body_h = max(1, self.body.winfo_height())
        span = self._content_h - body_h
        if span <= 6:
            return
        try:
            top = float(self.body.canvasy(0))
        except Exception:
            top = 0.0
        x = self.w - 3.6
        bot = body_h - GRIP - 4      # 给右下角抓手让位
        _rr(cv, x, top + 4, x + 2, top + bot, 1, fill="#161c24",
            outline="#161c24", tags=("sbar",))
        frac = min(1.0, body_h / max(1.0, self._content_h))
        th = max(28.0, (bot - 4) * frac)
        y0 = top + 4 + (bot - 4 - th) * max(0.0, min(1.0, top / span))
        _rr(cv, x - 0.6, y0, x + 2.6, y0 + th, 1.8, fill="#3a4552",
            outline="#3a4552", tags=("sbar",))

    # ------------------------------------------------------------ 头部 -----
    def _paint_head(self):
        vm = self.vm
        sig = (self.w, vm.get("win"), vm.get("loss"), vm.get("rate_txt"),
               vm.get("status"), vm.get("washed"), vm.get("res"),
               vm.get("pp_txt"), vm.get("ev_txt"), vm.get("bar"),
               self._reset_ask > 0,
               vm.get("mode"), vm.get("can_flip"), self.tab)
        if sig == self._head_sig:
            return
        self._head_sig = sig
        cv = self.head
        cv.delete("all")
        self._paint_tabs()
        w = self.w
        # 标题
        _rr(cv, 11, 8, 13.8, 26, 1.4, fill=GOLD_D, outline=GOLD_D)
        cv.create_text(21, TITLE_Y, text="记牌器", anchor="w", fill=GOLD,
                       font=self.f_title)
        if APP_VER:
            # 版本号贴在标题右边（小一号、灰一点），不占地方但一眼能看见
            try:
                _tx = 21 + self.f_title.measure("记牌器") + 5
            except Exception:
                _tx = 78
            cv.create_text(_tx, TITLE_Y + 1, text=APP_VER, anchor="w",
                           fill=DIM2, font=self.f_small)
        # 右上按钮
        cv.create_text(w - 44, TITLE_Y, text="AI", anchor="e", fill=GOLD_D,
                       font=self.f_small, tags=("btn_ai",))
        cv.create_text(w - 14, TITLE_Y, text="✕", anchor="e", fill=DIM,
                       font=self.f_small, tags=("btn_close",))
        cv.tag_bind("btn_ai", "<Button-1>", lambda e: self._click(self.on_ai))
        cv.tag_bind("btn_close", "<Button-1>",
                    lambda e: self._click(self.on_close))

        # 胜率条
        cy = (RATE_Y0 + RATE_Y1) / 2.0
        _rr(cv, 10, RATE_Y0, w - 10, RATE_Y1, 7, fill="#141a20",
            outline="#2a2721", width=1)
        cv.create_text(20, cy, text="胜率", anchor="w", fill=DIM,
                       font=self.f_small)
        rx = 20 + self.f_small.measure("胜率") + 8
        cv.create_text(rx, cy, text=vm.get("rate_txt") or "--",
                       anchor="w", fill=GOLD, font=self.f_rate)
        x = rx + self.f_rate.measure(vm.get("rate_txt") or "--") + 12
        cv.create_text(x, cy, text="%s胜" % (vm.get("win") or 0), anchor="w",
                       fill=GREEN, font=self.f_small)
        x += self.f_small.measure("%s胜" % (vm.get("win") or 0)) + 10
        cv.create_text(x, cy, text="%s负" % (vm.get("loss") or 0), anchor="w",
                       fill=RED, font=self.f_small)
        if self._reset_ask > time.time():
            # 二次确认条。原来「清空」是写死右对齐到 w-86 的，字号放大两号之后
            # 这两个词直接粘成一串（截图里读成「清空确认清空?」）。
            # 改成从右往左按实际字宽排，中间固定留 GAP；窗口窄到放不下就换短文案。
            left_end = x + self.f_small.measure("%s负" % (vm.get("loss") or 0))
            GAP = 18
            ask, yes = "清空战绩？", "确定"
            for a, y2 in (("清空战绩？", "确定"), ("清空？", "确定"),
                          ("清空？", "是")):
                need = self.f_small.measure(a) + GAP + self.f_small.measure(y2)
                if w - 20 - need >= left_end + 10:
                    ask, yes = a, y2
                    break
            aw = self.f_small.measure(ask)
            # 提示用灰、按钮用亮红：不然两个词都是一个红，看不出哪个能点
            cv.create_text(w - 20, cy, text=ask, anchor="e", fill=DIM2,
                           font=self.f_small, tags=("btn_no",))
            cv.create_text(w - 20 - aw - GAP, cy, text=yes, anchor="e",
                           fill="#ff9b8a", font=self.f_small, tags=("btn_yes",))
            cv.tag_bind("btn_yes", "<Button-1>", lambda e: self._do_reset())
            cv.tag_bind("btn_no", "<Button-1>", lambda e: self._cancel_reset())
        else:
            rx = w - 20
            if vm.get("can_flip"):
                # 刚记的那一局是「推定」的（认输 / 掉线，双方都还活着就结束了）：
                # 给它一个改判入口，点一下 负 ↔ 胜。
                cv.create_text(w - 20, cy, text="改判", anchor="e", fill=GOLD,
                               font=self.f_small, tags=("btn_flip",))
                cv.tag_bind("btn_flip", "<Button-1>",
                            lambda e: self._click(self.on_flip))
                rx = w - 20 - self.f_small.measure("改判") - 12
            cv.create_text(rx, cy, text="⟳ 重置", anchor="e", fill=DIM2,
                           font=self.f_small, tags=("btn_reset",))
            cv.tag_bind("btn_reset", "<Button-1>",
                        lambda e: self._ask_reset())

        # 资源行
        ry = RES_Y
        x = 12
        if vm.get("deck_left") is None:
            # 非对战：一行纯状态，不显示「牌库 0 手牌 0」这种假数据
            _st = vm.get("status") or "未在对战中"
            _has = self._paint_help(cv, ry, help_for(_st))
            _mw = w - 12 - (24 if _has else 0) - 12
            cv.create_text(12, ry, text=self._clip(_st, self.f_small, _mw),
                           anchor="w", fill=DIM, font=self.f_small)
            return
        washed = vm.get("washed")
        if washed:
            cv.create_text(x, ry, text="洗回", anchor="w", fill=RED,
                           font=self.f_small)
            x += self.f_small.measure("洗回") + 7
        cv.create_text(x, ry, text="牌库", anchor="w", fill=DIM,
                       font=self.f_small)
        x += self.f_small.measure("牌库") + 5
        cv.create_text(x, ry, text=str(vm.get("deck_left", 0)), anchor="w",
                       fill=GOLD, font=self.f_num)
        x += self.f_num.measure(str(vm.get("deck_left", 0))) + 10
        cv.create_text(x, ry, text="·", anchor="w", fill=RULE,
                       font=self.f_small)
        x += 9
        cv.create_text(x, ry, text="手牌", anchor="w", fill=DIM,
                       font=self.f_small)
        x += self.f_small.measure("手牌") + 5
        cv.create_text(x, ry, text=str(vm.get("hand_n", 0)), anchor="w",
                       fill=FG, font=self.f_num)
        if vm.get("pp_txt"):
            x += self.f_num.measure(str(vm.get("hand_n", 0))) + 10
            cv.create_text(x, ry, text="·", anchor="w", fill=RULE,
                           font=self.f_small)
            x += 9
            cv.create_text(x, ry, text="PP", anchor="w", fill=DIM,
                           font=self.f_small)
            x += self.f_small.measure("PP") + 5
            cv.create_text(x, ry, text=vm["pp_txt"], anchor="w", fill=DIM,
                           font=self.f_num)
            x += self.f_num.measure(vm["pp_txt"]) + 10
        # 进化点（2026-11-17）：我方「EP 1/2·超2/2」。探针读不到就没有这一段。
        if vm.get("ev_txt"):
            cv.create_text(x, ry, text="·", anchor="w", fill=RULE,
                           font=self.f_small)
            x += 9
            cv.create_text(x, ry, text="EP", anchor="w", fill=DIM,
                           font=self.f_small)
            x += self.f_small.measure("EP") + 5
            cv.create_text(x, ry, text=vm["ev_txt"], anchor="w",
                           fill=GOLD_D, font=self.f_row_small)
        if vm.get("status"):
            _has = self._paint_help(cv, ry, help_for(vm["status"]))
            _rx = w - 12 - (24 if _has else 0)
            cv.create_text(_rx, ry,
                           text=self._clip(vm["status"], self.f_small,
                                           max(40, _rx - x - 8)),
                           anchor="e", fill=DIM2, font=self.f_small)
        else:
            # 对战中没有提示文字，但异常状态照样要有「？」（例如读取已停）
            self._paint_help(cv, ry, None)
        # 进度条
        bar = float(vm.get("bar") or 0)
        _rr(cv, 12, BAR_Y, w - 12, BAR_Y + 3.4, 1.7, fill=TRACK, outline=TRACK)
        if bar > 0:
            _rr(cv, 12, BAR_Y, 12 + (w - 24) * min(1.0, bar), BAR_Y + 3.4,
                1.7, fill=GOLD_D, outline=GOLD_D)

    # ------------------------------------------------------------ 切换条 ---
    def _paint_tabs(self):
        """顶栏底部一条红线，两个标签压在线上：左「我方」右「对方」。

        线先整条画通，未选中的标签用背景色填充把线遮住 —— 看起来就是
        「红线中间挂着一个牌子」，选中态整块变红，一眼知道现在看的是谁。
        """
        cv = self.head
        w = self.w
        cy = HEAD_H - TAB_H / 2.0
        cv.delete("tabs")
        cv.create_line(10, cy, w - 10, cy, fill=TAB_RED, width=1.3,
                       tags=("tabs",))
        bh = 20.0
        gap = 8.0
        bw_self = self.f_tab.measure("我方") + 18
        bw_foe = self.f_tab.measure("对方") + 18
        mid = w / 2.0
        for side, x1, x2, label in (
                ("self", mid - gap / 2.0 - bw_self, mid - gap / 2.0, "我方"),
                ("foe", mid + gap / 2.0, mid + gap / 2.0 + bw_foe, "对方")):
            on = self.tab == side
            tag = "tab_" + side
            _rr(cv, x1, cy - bh / 2.0, x2, cy + bh / 2.0, 9,
                fill=(TAB_RED if on else BG),
                outline=(TAB_RED if on else "#3d424b"), width=1.2,
                tags=("tabs", tag))
            cv.create_text((x1 + x2) / 2.0, cy + 0.5, text=label,
                           fill=("#101419" if on else DIM),
                           font=self.f_tab, tags=("tabs", tag))
            cv.tag_bind(tag, "<Button-1>",
                        lambda e, s=side: self._set_tab(s))

    def _set_tab(self, side):
        """切标签。数据没变，只是换哪一组分区显示 —— 计数一直在跑。"""
        self._drag_block = time.time() + 0.35
        self._drag = None       # 清掉按下时记的偏移，别让它跟着下一次移动走
        if side == self.tab:
            return
        self._close_popup()
        self.tab = side
        self._head_sig = None
        self._sec_sig = None
        self._scroll_cur = self._scroll_tgt = 0.0
        try:
            self.body.yview_moveto(0)
        except Exception:
            pass
        self._last_h = 0        # 两组内容高度不同，强制重算窗口高度
        self.set_vm(self.vm)

    def _click(self, fn):
        self._drag_block = time.time() + 0.3
        if fn:
            fn()

    def _ask_reset(self):
        self._drag_block = time.time() + 0.3
        self._reset_ask = time.time() + 4.0
        self._head_sig = None
        self._paint_head()
        self._reset_after = self.root.after(4000, self._cancel_reset)

    def _cancel_reset(self):
        if self._reset_ask:
            self._reset_ask = 0.0
            self._head_sig = None
            self._paint_head()

    def _do_reset(self):
        self._reset_ask = 0.0
        self._head_sig = None
        if self.on_reset:
            self.on_reset()
        self._paint_head()

    # ------------------------------------------------------------ 高度 -----
    def _auto_h(self):
        body_h = min(self._content_h, self.max_body)
        return HEAD_H + int(max(140, body_h)) + 4

    def _apply_height(self):
        h = self.fixed_h or self._auto_h()
        if self._last_h and abs(h - self._last_h) < 3:
            return
        self._last_h = h
        self._set_geom(self.w, h)

    def _apply_scrollregion(self):
        body_h = max(1, self.body.winfo_height())
        total = max(self._content_h, body_h)
        self.body.configure(scrollregion=(0, 0, self.w, total))
        maxoff = max(0.0, self._content_h - body_h)
        if maxoff <= 0:
            self._scroll_cur = self._scroll_tgt = 0.0
            self.body.yview_moveto(0)

    # ------------------------------------------------------------ 动画 -----
    def _snap_row(self, row):
        """动画结束时按包围盒对齐一次：Tk 的 move 会取整，多帧累积会有 1~2px
        偏移，行距看起来忽宽忽窄。"""
        bb = self.body.bbox(row["key"])
        if not bb:
            return
        ex = row["x0"] - bb[0]
        ey = row["ty"] - bb[1]
        if abs(ex) >= 1 or abs(ey) >= 1:
            self._move_row(row, ex, ey)

    def _move_row(self, row, dx, dy):
        """整行位移。逐个 item 移，不用 tag —— tag 的浮点增量会被取整，
        累积几帧后行位置就和记录的目标值对不上了。"""
        for i in row["ids"].values():
            try:
                self.body.move(i, dx, dy)
            except Exception:
                pass

    def stop_timers(self):
        """关窗前把所有定时器停掉。

        以前只 cancel 了轮询那一个：动画的 16ms tick、卡图轮询、悬停大图重试、
        「重置」确认都会在 destroy 之后继续触发，Tcl 就刷
        `invalid command name ..._tick`。顺手把「还活着」标记关掉，
        免得 _tick 自己续命。
        """
        self._alive = False
        for attr in ("_art_timer", "_pv_after", "_reset_after", "_after",
                     "_help_after"):
            tid = getattr(self, attr, None)
            if not tid:
                continue
            try:
                self.root.after_cancel(tid)
            except Exception:
                pass
            try:
                setattr(self, attr, None)
            except Exception:
                pass
        # 「？」说明窗也是独立 Toplevel，关窗时一起收掉
        if getattr(self, "_hp", None) is not None:
            try:
                self._hp.destroy()
            except Exception:
                pass
            self._hp = None
            self._hp_lbl = None

    def _start_anim(self):
        if self._anim:
            return
        self._anim = True
        self.root.after(16, self._tick)

    def _tick(self):
        if not getattr(self, "_alive", True):
            return
        self._anim = False
        moving = False
        # 行位置插值
        for k, row in list(self.items.items()):
            if row.get("kind") != "row" or not row["ids"]:
                continue
            dx = row["tx"] - row["cx"]
            dy = row["ty"] - row["cy"]
            if abs(dx) < 0.4 and abs(dy) < 0.4:
                if dx or dy:
                    self._move_row(row, dx, dy)
                    row["cx"] = row["tx"]
                    row["cy"] = row["ty"]
                self._snap_row(row)
                continue
            sx, sy = dx * 0.42, dy * 0.42
            self._move_row(row, sx, sy)
            row["cx"] += sx
            row["cy"] += sy
            moving = True
        # 滚动缓动
        if abs(self._scroll_tgt - self._scroll_cur) > 0.0008:
            self._scroll_cur += (self._scroll_tgt - self._scroll_cur) * 0.34
            self._apply_yview()
            moving = True
        if moving:
            self._anim = True
            self.root.after(16, self._tick)

    def _apply_yview(self):
        try:
            self.body.yview_moveto(max(0.0, min(1.0, self._scroll_cur)))
        except Exception:
            pass
        self._paint_scrollbar()

    # ------------------------------------------------------------ 滚动 -----
    def _on_body_configure(self, _e=None):
        self._apply_scrollregion()
        self._paint_scrollbar()

    def _wheel(self, ev):
        body_h = max(1, self.body.winfo_height())
        if self._popup_open:                 # 滚动就把浮窗收起（它跟着世界坐标走）
            self._close_popup()
        if self._content_h - body_h <= 4:
            return
        step = -1 if ev.delta > 0 else 1
        span = max(1.0, self._content_h - body_h)
        self._scroll_tgt += step * 52.0 / span
        self._scroll_tgt = max(0.0, min(1.0, self._scroll_tgt))
        self._start_anim()

    # ------------------------------------------------------------ 缩放 -----
    def _paint_grip(self):
        g = self.grip
        g.delete("all")
        # 三条斜线：右下角，暗示可以往右下拖。
        # 原来用 #5a6675 的灰，贴在深色背景上几乎看不见（用户 2026-11-06 反馈），
        # 换成主题金 + 加粗，一眼能认出这儿能拖。
        for i in (0, 4.5, 9):
            g.create_line(GRIP - 2.5 - i, GRIP - 3, GRIP - 3, GRIP - 2.5 - i,
                          fill=GOLD, width=2, capstyle="round")

    def _grip_press(self, ev):
        self._drag_block = time.time() + 0.4
        self._rs = (ev.x_root, ev.y_root, self.root.winfo_width(),
                    self.root.winfo_height())
        self._rs_size = (self.w, self.fixed_h)

    def _grip_move(self, ev):
        if not self._rs:
            return
        x0, y0, w0, h0 = self._rs
        w = int(max(MIN_W, w0 + ev.x_root - x0))
        h = int(max(MIN_H, h0 + ev.y_root - y0))
        self._rs_size = (w, h)
        # 拖动过程只改窗口大小，松手才重排 —— 每帧重建行会卡
        self._set_geom(w, h)

    def _grip_release(self, _ev=None):
        size = self._rs_size
        self._rs = None
        if not size:
            return
        self.resize(size[0], size[1] or self.root.winfo_height())

    def _grip_auto(self, _ev=None):
        """双击抓手：高度回到自适应（内容多高就多高）。"""
        self._rs = None
        self.fixed_h = 0
        self._last_h = 0
        self.max_body = max(340, min(620,
                                     int(self.root.winfo_screenheight()) - 280))
        self._apply_height()
        self._apply_scrollregion()
        self._paint_scrollbar()
        if self.on_resize:
            try:
                self.on_resize(self.w, 0)
            except Exception:
                pass

    # ------------------------------------------------------------ 拖动 -----
    def _press(self, ev):
        if time.time() < self._drag_block:
            return
        self._drag = (ev.x_root - self.root.winfo_x(),
                      ev.y_root - self.root.winfo_y())

    def _dragmove(self, ev):
        if not self._drag:
            return
        try:
            self.root.geometry("+%d+%d" % (ev.x_root - self._drag[0],
                                           ev.y_root - self._drag[1]))
        except Exception:
            pass

    # ------------------------------------------------------------ 工具 -----
    def _clip(self, text, font, maxw):
        if maxw <= 8:
            return ""
        key = (text, id(font), int(maxw))
        hit = self._meas.get(key)
        if hit is not None:
            return hit
        out = text
        if font.measure(text) > maxw:
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if font.measure(text[:mid] + "…") <= maxw:
                    lo = mid
                else:
                    hi = mid - 1
            out = text[:lo] + "…"
        if len(self._meas) > 800:
            self._meas.clear()
        self._meas[key] = out
        return out

    def _wrap(self, text, font, maxw):
        """按像素宽度折行（中文逐字断，够用了）。窄窗口下 tips 自动变两行。

        折成两行后会把每行宽度匀一匀，否则最后一行常常只剩一两个字
        （300 宽时「…的衍生 / 物」很难看）。
        """
        if not text or maxw <= 20:
            return [text] if text else []
        key = ("wrap", text, id(font), int(maxw))
        hit = self._meas.get(key)
        if hit is not None:
            return hit

        def greedy(w):
            ls, cur = [], ""
            for ch in text:
                if cur and font.measure(cur + ch) > w:
                    ls.append(cur)
                    cur = ch
                else:
                    cur += ch
            if cur:
                ls.append(cur)
            return ls

        out = greedy(maxw)
        if len(out) > 1:
            per = sum(font.measure(l) for l in out) / float(len(out))
            alt = greedy(per * 1.03)
            if len(alt) <= len(out):          # 匀完之后行数别变多
                out = alt
        if len(self._meas) > 800:
            self._meas.clear()
        self._meas[key] = out
        return out


def demo():  # pragma: no cover - 手动预览用
    root = tk.Tk()
    root.overrideredirect(True)
    root.configure(bg=BG)
    root.geometry("320x600")
    hud = DeckHud(root, on_close=root.destroy,
                  on_resize=lambda w, h: print("resize", w, h))

    def row(cid, name, cost, kind, qty, playable=False, foe=False, zero=False):
        return {"cid": cid, "name": name, "cost": cost, "kind": kind,
                "qty": qty, "playable": playable, "foe": foe, "zero": zero}

    vm = {
        "mode": "battle", "win": 2, "loss": 1, "rate_txt": "66.7%",
        "deck_left": 21, "hand_n": 3, "pp_txt": "6/7", "bar": 0.52,
        "status": "", "washed": False, "res": 1,
        "sections": [
            {"key": "deck", "title": "牌库", "note": "16", "foldable": True,
             "side": "self",
             "items": [row(1, "人偶长矛手", 2, "随从", 2),
                       row(2, "青钱小卒", 1, "随从", 2),
                       row(3, "大游戏世界", 3, "护符", 1),
                       row(4, "跑酷", 1, "法术", 0, zero=True),
                       row(5, "宿命的罪人 · 卡忒斯特罗特", 8, "随从", 1)]},
            {"key": "hand", "title": "手牌", "note": "可打出 2", "side": "self",
             "items": [row(11, "人偶剧场", 2, "护符", 1, playable=True),
                       row(12, "诚心的尽小花", 1, "法术", 1, playable=True),
                       row(13, "白牙燎焚", 3, "法术", 1)]},
            {"key": "foe", "title": "对手已知", "note": "手牌 6", "tone": "foe",
             "side": "foe",
             "items": [row(21, "预视的魔法师", 3, "随从", 2, foe=True),
                       row(22, "冰结的唤术", 2, "法术", 1, foe=True)]},
        ],
    }
    hud.set_vm(vm)
    root.mainloop()


if __name__ == "__main__":
    demo()

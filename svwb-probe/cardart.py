# -*- coding: utf-8 -*-
"""卡图：卡牌 id → 官方图片，本地缓存 + 后台下载。

数据来源（2026-11-07 确认，官方站点，简体中文）：
  · 索引：https://shadowverse-wb.com/web/CardList/cardList?offset=N&include_token=1
          请求头 Lang: chs，返回的 card_image_hash 就是图片名
  · 图片：https://shadowverse-wb.com/uploads/card_image/chs/card/{hash}.png
          （每张 530×687 的 PNG，约 0.5 MB，所以只在本地缓存缩略图）

设计约束：
  · 全部非阻塞：界面线程只问「有没有」，没有就先空着，后台线程去下；
  · 断网 / 被墙 / 官方改了地址 → 一律当「暂时没有图」，不抛异常、不影响记牌器；
  · 只缓存缩略图（48px 行内图 / 380px 悬停图），不落原图 —— 一张原图 0.5 MB，
    打十几局就能吃掉几个 G；
  · 同一张图只下一次：下完立刻按各尺寸存好，之后任何尺寸都直接命中。
"""
import io
import json
import os
import queue
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(HERE, "gamedata", "card_images.json")
NAMES_PATH = os.path.join(HERE, "gamedata", "sv_card_names.json")
OPT_PATH = os.path.join(HERE, "logs", "hud_options.json")
CACHE_DIR = os.path.join(HERE, "cache", "cardart")

LANG = "chs"
BASE_URL = "https://shadowverse-wb.com/uploads/card_image/%s/card/%s.png"
# 需要的尺寸（像素高度）。一次下载全部生成，之后不再碰网络。
# 48 = 记牌器行内小图（deck_hud.ART_H），380 = 悬停大图（deck_hud.PREVIEW_PX）。
# 改这里必须同步 deck_hud 的那两个常量，否则会取不到图（_px 兜底会就近选档）。
SIZES = (48, 380)
ROW_PX = SIZES[0]
BIG_PX = SIZES[1]

_TIMEOUT = 15
_RETRY_AFTER = 600.0      # 失败后 10 分钟内不再重试（别对官方接口反复打）
_MAX_TRIES = 2
# 缩略图的底色：行内小图对齐记牌器里那块卡图底板，大图对齐记牌器背景色
_BG_ROW = (14, 20, 27)
_BG_BIG = (12, 16, 21)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_lock = threading.Lock()
_idx = [None]          # (hash_map, base_map, name_map)
_queued = set()        # 已排队的 hash
_failed = {}           # hash -> (次数, 时间)
_warned = [False]
_stats = {"ok": 0, "fail": 0}
_workers = []
_NWORK = 3             # 三路并发：一套卡组 40 张图从 90 秒缩到半分钟
_q = queue.Queue()


# ---------------------------------------------------------------- 选项 -----
def enabled():
    """记牌器要不要显示卡图（默认开）。改成 False 就彻底不下载、不显示。"""
    try:
        with open(OPT_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        return bool(d.get("art", True))
    except Exception:
        return True


def set_enabled(on):
    d = read_options()
    d["art"] = bool(on)
    try:
        os.makedirs(os.path.dirname(OPT_PATH), exist_ok=True)
        with open(OPT_PATH, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


def read_options():
    try:
        with open(OPT_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------- 索引 -----
def _load():
    """索引懒加载：card_id -> hash；再补一张「卡名 -> hash」的兜底表。

    官方卡表只列 982 张（每张卡一个条目），而游戏内存里的 id 有近 6000 个
    （各种异画 / 珍藏版 / 特殊效果版）。同一个 id 直接命中不了时，先按卡名
    找同名的卡，再按 id 结尾的版本位退回基础版 —— 这样常见的异画也都有图。
    """
    if _idx[0] is not None:
        return _idx[0]
    hmap, bmap, nmap, names = {}, {}, {}, {}
    try:
        with open(INDEX_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        hmap = d.get("hash") or {}
        bmap = d.get("base") or {}
    except Exception:
        hmap, bmap = {}, {}
    # 卡名兜底：名字来自同目录的卡表（5910 条），一张卡有多个 id 时取第一个
    try:
        with open(NAMES_PATH, encoding="utf-8") as fh:
            names = json.load(fh)
        for cid, h in hmap.items():
            nm = names.get(cid) or ""
            if nm and nm not in nmap:
                nmap[nm] = h
    except Exception:
        pass
    _idx[0] = (hmap, bmap, nmap, names)
    return _idx[0]


def hash_for(cid):
    """这张卡用哪张图。找不到返回 None（界面就不画图，只画字）。"""
    hmap, bmap, nmap, names = _load()
    if not hmap:
        return None
    s = str(cid)
    for k in (s, s.zfill(8)):
        if k in hmap:
            return hmap[k]
    # 珍藏 / 异画：官方记录里带 base_card_id
    b = bmap.get(s)
    if b and b in hmap:
        return hmap[b]
    # id 末位是版本位，退回基础版再试
    for cand in (s[:-1] + "0", s[:-2] + "10", s[:-1] + "1"):
        if cand in hmap:
            return hmap[cand]
    # 同名的卡（异画、加护、纹章之类）
    nm = names.get(s) or ""
    if nm and nm in nmap:
        return nmap[nm]
    return None


# ---------------------------------------------------------------- 缓存 -----
def _px(px):
    """把请求的尺寸落到最近的已生成档位。

    调用方（记牌器）写的是像素高度，如果它跟 SIZES 没对齐，以前会永远取不到
    图、还白排一次队列。这里就近取档，最差是图小一点，不会再「没有图」。
    """
    try:
        v = int(px)
    except Exception:
        return ROW_PX
    return min(SIZES, key=lambda s: abs(s - v))


def cache_path(h, px):
    return os.path.join(CACHE_DIR, "%s@%d.png" % (h, _px(px)))


def cached(h):
    """这张图各尺寸里最全的那个缓存文件路径（没有就 None）。"""
    for px in SIZES:
        p = cache_path(h, px)
        if os.path.isfile(p):
            return p
    return None


def has(cid):
    """这张卡在官方卡表里有没有对应的图（有图才给它留位置）。"""
    return bool(hash_for(cid))


def get(cid, px=ROW_PX):
    """要一张图：缓存里有就返回路径，没有就排进下载队列并返回 None。不阻塞。"""
    if not enabled():
        return None
    h = hash_for(cid)
    if not h:
        return None
    p = cache_path(h, px)
    if os.path.isfile(p):
        return p
    _enqueue(h)
    return None


def _enqueue(h):
    with _lock:
        if h in _queued:
            return
        st, at = _failed.get(h, (0, 0.0))
        if st >= _MAX_TRIES and (time.time() - at) < _RETRY_AFTER:
            return
        _queued.add(h)
    _q.put(h)
    _ensure_worker()


# ---------------------------------------------------------------- 下载 -----
def _ensure_worker():
    with _lock:
        _workers[:] = [t for t in _workers if t.is_alive()]
        # 队列里还有活就补齐线程；闲着的时候不要一直占着
        need = min(_NWORK, _q.qsize()) - len(_workers)
        spawn = []
        for _ in range(max(0, need)):
            t = threading.Thread(target=_loop, name="cardart", daemon=True)
            _workers.append(t)
            spawn.append(t)
    for t in spawn:
        t.start()


def _loop():
    while True:
        try:
            h = _q.get(timeout=30)
        except queue.Empty:
            with _lock:
                _workers[:] = [t for t in _workers
                               if t.is_alive() and t is not threading.current_thread()]
                if _q.empty():
                    return
            continue
        try:
            _fetch(h)
        except Exception:
            pass
        finally:
            with _lock:
                _queued.discard(h)
        time.sleep(0.05)          # 别把官方站点当 CDN 刷


def _fetch(h):
    """下一张原图，按 SIZES 全部缩好存盘。失败就记账，不再重试。"""
    try:
        import urllib.request
        req = urllib.request.Request(BASE_URL % (LANG, h),
                                     headers={"User-Agent": _UA, "Lang": LANG})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = r.read()
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        os.makedirs(CACHE_DIR, exist_ok=True)
        for px in SIZES:
            w = max(1, int(round(im.width * px / float(im.height))))
            small = im.resize((w, px), Image.LANCZOS)
            # 存成不透明：官方图四角是透的，Tk 的 PhotoImage 遇到 alpha 会把
            # 下面的窗口透出来（悬停大图尤其明显）。按各自实际底色调平。
            bg = _BG_BIG if px == BIG_PX else _BG_ROW
            flat = Image.new("RGB", small.size, bg)
            flat.paste(small, (0, 0), small)
            flat.save(cache_path(h, px))
        with _lock:
            _stats["ok"] += 1
            _failed.pop(h, None)
        return True
    except Exception as e:
        with _lock:
            _stats["fail"] += 1
            st = _failed.get(h, (0, 0.0))[0] + 1
            _failed[h] = (st, time.time())
        if not _warned[0]:
            _warned[0] = True
            print("卡图下载失败（功能不受影响，只是没有图）：%s" % e, flush=True)
        return False


# ---------------------------------------------------------------- 杂项 -----
def stats():
    with _lock:
        return {"ok": _stats["ok"], "fail": _stats["fail"],
                "queued": len(_queued)}


def prefetch(cids, px=ROW_PX):
    """批量排队（导入卡组后预热用）。"""
    if not enabled():
        return 0
    n = 0
    for c in cids:
        h = hash_for(c)
        if h and not os.path.isfile(cache_path(h, px)):
            _enqueue(h)
            n += 1
    return n


def cached_count():
    try:
        return len([f for f in os.listdir(CACHE_DIR) if f.endswith(".png")])
    except Exception:
        return 0


def clear_cache():
    """清空本地卡图缓存（磁盘占用出问题时用）。"""
    n = 0
    try:
        for f in os.listdir(CACHE_DIR):
            if f.endswith(".png"):
                try:
                    os.remove(os.path.join(CACHE_DIR, f))
                    n += 1
                except Exception:
                    pass
    except Exception:
        pass
    return n


def cache_size_mb():
    total = 0
    try:
        for f in os.listdir(CACHE_DIR):
            try:
                total += os.path.getsize(os.path.join(CACHE_DIR, f))
            except Exception:
                pass
    except Exception:
        pass
    return total / 1048576.0

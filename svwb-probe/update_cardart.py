# -*- coding: utf-8 -*-
"""更新卡图索引 / 预下载卡图。

出新卡包之后跑一次就行：

    python update_cardart.py            # 只刷新索引（几十秒，1 MB 流量）
    python update_cardart.py --fetch    # 刷新索引 + 把所有卡图缩略图都下下来

索引来自官方卡表接口（只读公开数据，不需要登录）：
    https://shadowverse-wb.com/web/CardList/cardList?offset=N&include_token=1
    请求头 Lang: chs → 简体中文

图片地址：
    https://shadowverse-wb.com/uploads/card_image/chs/card/{hash}.png
"""
import io
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "gamedata", "card_images.json")
BASE = "https://shadowverse-wb.com/web/CardList/cardList?offset=%d&include_token=1"

LANG = "chs"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def get_page(offset):
    req = urllib.request.Request(BASE % offset,
                                 headers={"User-Agent": UA, "Lang": LANG})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))["data"]


def refresh_index():
    """官方接口是按「卡序」窗口返回的，且窗口边界会有一两格的漂移，
    所以用 16 的步长（一次拿 32 张，相邻两页重叠一半）扫一遍，凑齐全部卡。"""
    hmap, bmap, sets = {}, {}, {}
    total = 0
    empty = 0
    off = 0
    while off < 8000:
        try:
            d = get_page(off)
        except Exception as e:
            print("  取第 %d 页失败：%s" % (off, e))
            off += 16
            time.sleep(0.5)
            continue
        det = d.get("card_details") or {}
        sets.update(d.get("card_set_names") or {})
        total = d.get("count") or total
        for cid, row in det.items():
            c = row.get("common") or {}
            h = c.get("card_image_hash")
            if not h:
                continue
            hmap[cid] = h
            b = str(c.get("base_card_id") or "")
            if b and b != cid:
                bmap[cid] = b
        if not det:
            empty += 1
            if empty >= 3:
                break
        else:
            empty = 0
        sys.stdout.write("\r  已收集 %d / %d 张…" % (len(hmap), total or 0))
        sys.stdout.flush()
        off += 16
        time.sleep(0.08)
    print()
    doc = {"lang": LANG, "at": time.strftime("%Y-%m-%d"),
           "source": BASE % 0, "hash": hmap, "base": bmap, "sets": sets}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, OUT)
    print("索引已更新：%d 张卡 → %s" % (len(hmap), OUT))
    return hmap


def fetch_all(only_missing=True):
    import cardart
    hmap = (json.load(io.open(OUT, encoding="utf-8")) or {}).get("hash") or {}
    n = len(hmap)
    print("开始下载 %d 张卡图（每张约 0.5 MB，只留缩略图）…" % n)
    t0 = time.time()
    for i, (cid, h) in enumerate(hmap.items(), 1):
        p = cardart.cache_path(h, cardart.ROW_PX)
        if only_missing and os.path.isfile(p):
            continue
        cardart._fetch(h)
        if i % 20 == 0:
            sys.stdout.write("\r  %d / %d（%.0f%%）已用 %.0f 秒"
                             % (i, n, 100.0 * i / n, time.time() - t0))
            sys.stdout.flush()
    print("\n完成，本地缓存 %.1f MB" % cardart.cache_size_mb())


def main():
    args = sys.argv[1:]
    if "--fetch" in args:
        refresh_index()
        fetch_all()
    elif "--index" in args or not args:
        refresh_index()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

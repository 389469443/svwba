#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""刷新卡表元数据（gamedata/sv_card_meta.json + sv_card_names.json）。

出了新卡包之后跑一次：

    python update_cardmeta.py            # 刷新（合并进现有表，旧卡不删）
    python update_cardmeta.py --check    # 只对比，不写盘（看看会改动什么）

数据来源：官方卡表接口（只读公开数据，不需要登录）
    https://shadowverse-wb.com/web/CardList/cardList?offset=N&include_token=1
    请求头 Lang: chs → 简体中文

**是合并，不是重建**：接口只给得到 900 多张「当前能在对局里出现」的卡；
本地这份 5900 多条的 meta 里还有大量异画/其它形态的 id 变体（66…/70…/86…）
和纹章/信仰/加护（type 5~12），接口里根本没有。所以只更新接口给到的那些 id，
其余原样保留 —— 用这些变体照旧能查到名字，不会因为刷新反而丢卡。

写入字段与旧表一致：name / cost / rarity / atk / life / type / class / skill，
其中 skill 就是技能原文（带 <color=Keyword> 标记和 <hr><ev> 进化段），
下游（AI 陪玩、对手获得推测）直接按文本解析，别在这里做清洗。
"""
import io
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
META = os.path.join(HERE, "gamedata", "sv_card_meta.json")
NAMES = os.path.join(HERE, "gamedata", "sv_card_names.json")

BASE = "https://shadowverse-wb.com/web/CardList/cardList?offset=%d&include_token=1"
LANG = "chs"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
STEP = 24          # 官方分页边界会漂一两格，步长小于页容量（32）才能不漏
MAX_OFFSET = 2000
SLEEP = 0.06


def get_page(offset):
    req = urllib.request.Request(BASE % offset,
                                 headers={"User-Agent": UA, "Lang": LANG})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))["data"]


def fetch_all(verbose=True):
    """把接口里的卡全抓下来 → {id: entry}。"""
    out, empty, off, total = {}, 0, 0, 0
    t0 = time.time()
    while off < MAX_OFFSET and empty < 3:
        try:
            d = get_page(off)
        except Exception as e:
            if verbose:
                print("  第 %d 页失败（%s），跳过" % (off, e))
            off += STEP
            time.sleep(0.4)
            continue
        det = d.get("card_details") or {}
        total = d.get("count") or total
        if det:
            empty = 0
            for cid, row in det.items():
                e = _entry(row)
                if e:
                    out[cid] = e
        else:
            empty += 1
        off += STEP
        if verbose and off % 240 == 0:
            print("  … 已抓 %d 张（%.0f 秒）" % (len(out), time.time() - t0))
        time.sleep(SLEEP)
    if verbose:
        print("  接口共 %s 张，实抓 %d 张，用时 %.0f 秒"
              % (total or "?", len(out), time.time() - t0))
    return out


def _entry(row):
    c = row.get("common") or {}
    name = (c.get("name") or "").strip()
    if not name:
        return None
    return {"name": name,
            "cost": c.get("cost"),
            "rarity": c.get("rarity"),
            "atk": c.get("atk"),
            "life": c.get("life"),
            "type": c.get("type"),
            "class": c.get("class"),
            "skill": c.get("skill_text") or ""}


def _load(path, default):
    try:
        with io.open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else default
    except Exception:
        return default


def _save(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=0)
    os.replace(tmp, path)


def diff(fresh, old):
    added = [k for k in fresh if k not in old]
    changed = [k for k in fresh if k in old and fresh[k] != old[k]]
    gone = [k for k in old if k not in fresh]
    return added, changed, gone


def main():
    args = sys.argv[1:]
    check = "--check" in args
    print("拉取官方卡表…")
    fresh = fetch_all()
    if not fresh:
        print("一张都没抓到：检查网络 / 接口是否改版。本次不做任何改动。")
        return 2

    old = _load(META, {})
    added, changed, gone = diff(fresh, old)
    print("对比本地卡表：")
    print("  接口 %d 张 / 本地 %d 条" % (len(fresh), len(old)))
    print("  新增 %d、字段有变 %d、接口里没有（原样保留）%d"
          % (len(added), len(changed), len(gone)))
    for k in added[:8]:
        print("    + %s %s" % (k, fresh[k]["name"]))
    for k in changed[:8]:
        a, b = old[k], fresh[k]
        keys = [x for x in a if x in b and a[x] != b[x]]
        print("    ~ %s %s（%s）" % (k, b["name"], "、".join(keys) or "字段"))
    if check:
        print("--check：不写盘。")
        return 0
    if not added and not changed:
        print("本地已是最新，无需改动。")
        return 0

    old.update(fresh)
    _save(META, old)
    names = _load(NAMES, {})
    for cid, e in fresh.items():
        names[cid] = e["name"]
    _save(NAMES, names)
    print("已写入：%s（%d 条）" % (META, len(old)))
    print("已写入：%s（%d 条）" % (NAMES, len(names)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

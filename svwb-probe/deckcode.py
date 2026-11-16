#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""影之诗（国服）卡组码解码器。

卡组码格式（本地可解，不需要联网）：
    #巫师(2)#
    #指定系列#
    #巫师#
    1.3.g5Rs.g5Rs.g5Rs.cH1g...
    #在游戏中点击【卡牌】-【新牌组】-【使用牌组码】进行粘贴 #

规则：
- `模式.职业.` 是卡组码头部（用户 2026-10-30 确认）：
  模式 1=指定系列、2=无限制、3=无禁止、4=基础系列；
  职业 1=妖精、2=皇家、3=巫师、4=龙族、5=梦魇、6=主教、7=超越者。
  例：`1.3.` = 指定系列 · 巫师。
- 之后每 **4 个字符** 是一张卡的编码，重复几次就是几张。
- 4 字符 → 24 bit → CardId，字母表是「数字在前」：
      "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
  即 0-9 → 0..9，A-Z → 10..35，a-z → 36..61。

用法：
    python deckcode.py --code "1.3.g5Rs..."        # 直接传码
    python deckcode.py --file code.txt             # 从文件读（含 #...# 包裹也行）
    python deckcode.py --code "..." --json         # 输出 JSON 供程序用
    python deckcode.py --encode 11032310 10031210  # 反向：卡 id → 码段（调试用）
"""
import argparse
import json
import re
import sys

ALPHA = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
FULL = ALPHA + "-_"          # 62 = '-', 63 = '_'（国服码里出现过 '-'）
IDX = {c: i for i, c in enumerate(FULL)}

# 卡组码头部：模式 . 职业
MODES = {1: "指定系列", 2: "无限制", 3: "无禁止", 4: "基础系列"}
CLASSES = {1: "妖精", 2: "皇家", 3: "巫师", 4: "龙族",
           5: "梦魇", 6: "主教", 7: "超越者"}

_HEAD_RE = re.compile(r"^\s*(\d+)\.(\d+)\.")


def parse_header(code):
    """卡组码头部 `模式.职业` → (模式名, 职业名)；解析不了返回 (None, None)。"""
    m = _HEAD_RE.match(code or "")
    if not m:
        return None, None
    mode, cls = int(m.group(1)), int(m.group(2))
    return (MODES.get(mode, "模式%d" % mode),
            CLASSES.get(cls, "职业%d" % cls))


def _find_code_line(text):
    """从 #...# 包裹的多行文本里找出 `模式.职业.` 开头的卡组码行。"""
    for line in (text or "").splitlines():
        line = line.strip()
        if _HEAD_RE.match(line):
            return line
    # 没有换行就整段找
    m = _HEAD_RE.search(text or "")
    if m:
        return text[m.start():].strip().strip("#").strip()
    return ""


def parse_name(text):
    """卡组名：#巫师(2)# 这种自定义名优先；没有就用「模式·职业」（如 指定系列·巫师）。"""
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("#") and line.endswith("#") and len(line) > 2:
            name = line.strip("#").strip()
            if name and not name.startswith("指定") \
                    and not name.startswith("在游戏"):
                return name
    try:
        mode, cls = parse_header(_find_code_line(text))
        if mode and cls:
            return "%s·%s" % (mode, cls)
    except Exception:
        pass
    return "导入的卡组"


def decode_seg(s):
    """4 或 5 字符 → CardId（4 字符=24bit 旧码；5 字符=30bit 国服新码）。"""
    if len(s) not in (4, 5):
        raise ValueError("bad segment: %r" % s)
    v = 0
    for c in s:
        if c not in IDX:
            raise ValueError("bad char %r in %r" % (c, s))
        v = (v << 6) | IDX[c]
    return v


def decode(text):
    """返回 [(card_id, count), ...]（按码里的顺序）。"""
    line = _find_code_line(text)
    if not line:
        raise ValueError("找不到「模式.职业.」开头的卡组码")
    m = _HEAD_RE.match(line)
    if not m:
        raise ValueError("不是「模式.职业.」格式的卡组码（如 1.3.）")
    segs = [s for s in line[m.end():].split(".") if s]
    out = []
    for s in segs:
        if len(s) < 4:
            continue        # 复制不全的尾巴（如最后的 '5'）直接忽略
        cid = decode_seg(s)
        if out and out[-1][0] == cid:
            out[-1] = (cid, out[-1][1] + 1)
        else:
            out.append((cid, 1))
    return out


def encode_seg(card_id):
    """CardId → 卡组码段（反向）。

    4 字符 = 24 bit（旧码，常见卡 id），5 字符 = 30 bit（国服新码）。
    之前只支持 24 bit，卡 id >= 16,777,216 就直接报错
    （90021320 这类特殊卡会炸），和 decode_seg 支持的 5 字符对不上。
    """
    if not (0 <= card_id < (1 << 30)):
        raise ValueError("card id 超出 30bit: %r" % card_id)
    width = 4 if card_id < (1 << 24) else 5
    out = []
    for shift in range((width - 1) * 6, -1, -6):
        out.append(FULL[(card_id >> shift) & 63])
    return "".join(out)


def encode(entries, prefix="1.3"):
    """[(card_id, count)] → 卡组码行。"""
    segs = []
    for cid, n in entries:
        segs.extend([encode_seg(cid)] * int(n))
    return prefix + "." + ".".join(segs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", help="卡组码文本（可带 #...# 包裹）")
    ap.add_argument("--file", help="从文件读卡组码")
    ap.add_argument("--json", action="store_true", help="输出 JSON（id/count）")
    ap.add_argument("--encode", nargs="+", type=int, help="反向编码卡 id 列表")
    args = ap.parse_args()

    if args.encode:
        print(encode([(cid, 1) for cid in args.encode]))
        return 0

    text = args.code or ""
    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read()
    if not text:
        print("需要 --code 或 --file", file=sys.stderr)
        return 1
    try:
        entries = decode(text)
    except Exception as e:
        print("解码失败：%s" % e, file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps([{"id": cid, "count": n} for cid, n in entries],
                         ensure_ascii=False))
        return 0
    total = sum(n for _, n in entries)
    for cid, n in entries:
        print("%d ×%d" % (cid, n))
    print("合计：%d 张" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())

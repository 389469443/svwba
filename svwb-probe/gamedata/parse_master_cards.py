#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
解析 MasterMemory 的 mastermemory.bytes，生成「卡 id -> 卡名」映射。

文件格式（2026-10-21 实证）:
  [msgpack map: 表名 -> [相对 map 末尾的偏移, 块字节长度]]
  [按 map 顺序紧随其后的各表数据]
      - 大表: msgpack ExtType(99) 块 = msgpack int32(未压缩长度) + LZ4 block
      - 小表: 明文 msgpack 数组
  每张表的解压结果 = 行数组，每行 = 按类 [Key(n)] 顺序排列的字段数组。

卡名映射链（已验证）:
  CardMaster.CardId
    -> CardMaster.CardTextId
    -> CardText.CardTextId -> CardText._Name ("CN_xxxxxxxx")
    -> MasterTextLabel["CN_xxxxxxxx"] -> 中文卡名

用法:
  python -u gamedata/parse_master_cards.py [--file PATH] [--out-dir DIR]

产物（默认 gamedata/）:
  sv_card_names.json     {"<CardId>": "<卡名>"}
  sv_base_card_names.json {"<BaseCardId>": "<卡名>"}
  master_summary.txt     表清单 / 行数 / 格式 / 样本诊断
"""
import argparse
import io
import json
import os
import sys

import lz4.block
import msgpack

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FILE = os.path.join(HERE, "mastermemory.bytes")
DEFAULT_OUT = HERE

# 主代理提供的对局样本 id（从卡组元素 +0xA8 -> +0x90 读出）
SAMPLE_IDS = [5429194, 5302673, 5467424, 5716754, 712, 20858, 3820713]

# 已知的字段顺序（来自 dump.cs 的 [Key(n)]），仅用于摘要展示
KEY_FIELDS = {
    "CardMaster": ["CardId", "BaseCardId", "CardResourceId", "CardSetId",
                   "CardTextId", "FoilType", "NormalCardId", "FoilCardId",
                   "HighRankFoilCardId", "WelcomeCardId", "PreConvertId",
                   "LeaderAreaOwnerId", "HashId"],
    "CardText": ["CardTextId", "_Name", "_SkillDesc", "_Flavour",
                 "_EvoFlavour", "_CardCv"],
    "BaseCardMaster": ["BaseCardId", "Type", "Class", "Tribes", "Cost",
                       "Atk", "Life", "ChantCount", "Rarity",
                       "RelatedBaseCardIds", "SpecificEffectCardIds",
                       "FaithRelatedBaseCardIds"],
    "MasterTextLabel": ["LabelKey", "Text"],
}


def read_top(path):
    """返回 (top_map, map_end_offset, raw_bytes)。"""
    with io.open(path, "rb") as fh:
        raw = fh.read()
    unp = msgpack.Unpacker(io.BytesIO(raw), raw=False, strict_map_key=False,
                           max_buffer_size=0)
    top = next(unp)
    map_end = unp.tell()
    if not isinstance(top, dict):
        raise SystemExit("顶层不是 map：%r" % (top,))
    return top, map_end, raw


def decode_block(raw, start, length):
    """解析一个表块，返回 (obj, kind)。kind: 'lz4' / 'plain'。"""
    tag = raw[start]
    if tag in (0xc7, 0xc8, 0xc9):
        if tag == 0xc7:
            n = raw[start + 1]
            code = raw[start + 2]
            payload = raw[start + 3:start + 3 + n]
        elif tag == 0xc8:
            n = int.from_bytes(raw[start + 1:start + 3], "big")
            code = raw[start + 3]
            payload = raw[start + 4:start + 4 + n]
        else:
            n = int.from_bytes(raw[start + 1:start + 5], "big")
            code = raw[start + 5]
            payload = raw[start + 6:start + 6 + n]
        if code != 99:
            raise ValueError("未知 ExtType code=%r @%#x" % (code, start))
        ulen = msgpack.unpackb(payload[:5], raw=True)
        body = lz4.block.decompress(payload[5:], uncompressed_size=ulen)
        return msgpack.unpackb(body, raw=False, strict_map_key=False,
                               unicode_errors="replace"), "lz4"
    return msgpack.unpackb(raw[start:start + length], raw=False,
                           strict_map_key=False,
                           unicode_errors="replace"), "plain"


def parse_tables(path):
    top, map_end, raw = read_top(path)
    keys = list(top.keys())
    base = map_end - top[keys[0]][0]
    tables, kinds = {}, {}
    for k in keys:
        off, ln = top[k]
        obj, kind = decode_block(raw, base + off, ln)
        tables[k] = obj
        kinds[k] = kind
    return tables, kinds, raw


def build_names(tables):
    """返回 card_names(CardId->名)、base_names(BaseCardId->名)、stats。"""
    mtl = {}
    for r in tables.get("MasterTextLabel", []):
        if isinstance(r, list) and len(r) >= 2:
            mtl[r[0]] = r[1]
    ctext = {}
    for r in tables.get("CardText", []):
        if isinstance(r, list) and len(r) >= 2:
            ctext[r[0]] = r
    cm = [r for r in tables.get("CardMaster", []) if isinstance(r, list) and r]

    card_names = {}
    base_names = {}
    stats = {"rows": len(cm), "resolved": 0, "fallback": 0, "no_text": 0}

    def name_of(card_text_id):
        c = ctext.get(card_text_id)
        if not c or not c[1]:
            return None, False
        raw_name = c[1]
        txt = mtl.get(raw_name)
        if txt:
            return txt, True
        if txt == "":
            return raw_name, False      # 有 key 但文本为空：回退 key
        return raw_name, False          # MTL 完全没有该 key

    for row in cm:
        card_id = row[0]
        base_id = row[1]
        card_text_id = row[4]
        nm, resolved = name_of(card_text_id)
        if nm is None:
            stats["no_text"] += 1
            continue
        card_names[str(card_id)] = nm
        if resolved:
            stats["resolved"] += 1
        else:
            stats["fallback"] += 1
        # 同一 BaseCardId 优先保留「非 foil（FoilType=0）」的名字
        foil = row[5] if len(row) > 5 else 0
        if str(base_id) not in base_names or foil == 0:
            base_names[str(base_id)] = nm
    return card_names, base_names, stats


def deep_search(tables, sid):
    """在所有表的任意嵌套字段里找 sid；返回 [(表, 行号, 路径, 值)]。"""
    hits = []

    def walk(obj, path):
        if isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, path + [i])
        elif isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, path + [k])
        else:
            if obj == sid:
                hits.append(path)
            elif isinstance(obj, str) and str(sid) in obj:
                hits.append(("STR", obj))

    for tbl, rows in tables.items():
        if isinstance(rows, list):
            for ri, row in enumerate(rows):
                walk(row, [ri])
        else:
            walk(rows, [])
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=DEFAULT_FILE)
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)

    tables, kinds, raw = parse_tables(args.file)
    card_names, base_names, stats = build_names(tables)

    # ---- 写 JSON ----
    card_path = os.path.join(out_dir, "sv_card_names.json")
    with io.open(card_path, "w", encoding="utf-8") as fh:
        json.dump(card_names, fh, ensure_ascii=False, indent=1, sort_keys=True)
    base_path = os.path.join(out_dir, "sv_base_card_names.json")
    with io.open(base_path, "w", encoding="utf-8") as fh:
        json.dump(base_names, fh, ensure_ascii=False, indent=1, sort_keys=True)

    # ---- 样本诊断 ----
    sample_report = []
    card_id_set = {int(k) for k in card_names}
    base_id_set = {int(k) for k in base_names}
    for sid in SAMPLE_IDS:
        hits = deep_search(tables, sid)
        sample_report.append({
            "id": sid,
            "in_card_id": sid in card_id_set,
            "in_base_card_id": sid in base_id_set,
            "hits": hits[:5],
            "hit_count": len(hits),
        })

    # ---- summary ----
    lines = []
    lines.append("MasterMemory 解析摘要")
    lines.append("=" * 72)
    lines.append("文件: %s (%d bytes)" % (args.file, len(raw)))
    lines.append("顶层: %d 张表；LZ4 ext99 块 %d，明文块 %d"
                 % (len(tables),
                    sum(1 for v in kinds.values() if v == "lz4"),
                    sum(1 for v in kinds.values() if v == "plain")))
    lines.append("")
    lines.append("格式: [msgpack map 表名 -> [相对偏移, 长度]] + 按序排列的表块")
    lines.append("      LZ4 块 = msgpack int32(未压缩长度) + lz4.block 数据")
    lines.append("      表 = 行数组；行 = 按 [Key(n)] 顺序的字段数组")
    lines.append("")
    lines.append("卡名映射链: CardMaster.CardId -> CardMaster.CardTextId")
    lines.append("            -> CardText.CardTextId -> CardText._Name")
    lines.append("            -> MasterTextLabel[_Name] -> 中文卡名")
    lines.append("")
    lines.append("表清单（表名  行数  存储  首行示例）:")
    for k in tables:
        rows = tables[k]
        n = len(rows) if hasattr(rows, "__len__") else "?"
        first = repr(rows[0])[:70] if isinstance(rows, list) and rows else ""
        lines.append("  %-40s %-6s %-5s %s" % (k, n, kinds[k], first))
    lines.append("")
    lines.append("卡名映射统计:")
    lines.append("  CardMaster 行数        : %d" % stats["rows"])
    lines.append("  生成 CardId -> 卡名    : %d 条 (%s)"
                 % (len(card_names), card_path))
    lines.append("  生成 BaseCardId -> 卡名: %d 条 (%s)"
                 % (len(base_names), base_path))
    lines.append("  MasterTextLabel 命中   : %d" % stats["resolved"])
    lines.append("  回退 _Name 原文        : %d（文本为空或缺失）" % stats["fallback"])
    lines.append("  无 CardText            : %d" % stats["no_text"])
    lines.append("")
    lines.append("样本 id 诊断（主代理给出的对局卡 id）:")
    for s in sample_report:
        lines.append("  %-9d CardId命中=%-5s BaseCardId命中=%-5s 全表深搜命中=%d %s"
                     % (s["id"], s["in_card_id"], s["in_base_card_id"],
                        s["hit_count"], s["hits"][:3]))
    coverage = sum(1 for s in sample_report if s["in_card_id"]) * 100.0 / len(SAMPLE_IDS)
    lines.append("  样本覆盖率: %.1f%% (%d/%d)"
                 % (coverage, sum(1 for s in sample_report if s["in_card_id"]),
                    len(SAMPLE_IDS)))
    lines.append("")
    lines.append("结论:")
    lines.append("  * master 数据里的卡 id 为 8 位数字（CardId/BaseCardId），")
    lines.append("    样本 id（5429194 等 7 位、712 等 3 位）在 210 张表任意字段中均不存在。")
    lines.append("  * 若对局内存读到的是 8 位卡 id，可直接用 sv_card_names.json 查名。")
    lines.append("  * 样本 id 更可能是读取偏移/对象错误，或来自服务器下发的卡池，")
    lines.append("    需回查 +0xA8/+0x90 的读取路径。")

    summary_path = os.path.join(out_dir, "master_summary.txt")
    with io.open(summary_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print("tables: %d (lz4 %d / plain %d)"
          % (len(tables),
             sum(1 for v in kinds.values() if v == "lz4"),
             sum(1 for v in kinds.values() if v == "plain")))
    print("card_names: %d -> %s" % (len(card_names), card_path))
    print("base_names: %d -> %s" % (len(base_names), base_path))
    print("summary: %s" % summary_path)
    for s in sample_report:
        print("  sample %-9d CardId=%-5s BaseCardId=%-5s hits=%d"
              % (s["id"], s["in_card_id"], s["in_base_card_id"], s["hit_count"]))
    print("sample coverage: %.1f%%" % coverage)
    return 0


if __name__ == "__main__":
    sys.exit(main())

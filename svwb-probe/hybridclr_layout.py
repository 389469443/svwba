#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HybridCLR 解释器类型的**布局反解工具**。

背景（为什么需要它）：
  · 游戏用的是 HybridCLR 热更新，`Netease.GameNet.*` / `Sv2Warchess.*` 这些类型
    是**运行时注册的解释器类型**，不是 AOT 类型
  · 它们的字段布局不能靠 `Il2CppDumper` 的静态偏移表，也不能靠"声明顺序"硬猜
  · 之前反复读到垃圾值（卡 ID 命中卡库 0%），根因就是布局猜错

本工具的做法：**用已知真值反解布局**，而不是猜。
  1. 从 `_types_warchess.txt`（dnmeta 从热更新程序集解出的元数据）拿到类的字段
     **名称和类型**，按声明顺序
  2. 生成多种布局假设：声明顺序 / 按宽度降序 / 每字段一个 8 字节槽 / 紧凑打包
  3. 用用户给出的**已知值**（如"血量=24、等级=6"）逐个验证
  4. 假设全错时，进入**暴力反解**：
       对每个已知字段，在实例内存里搜索所有出现该值的位置（按 int16/int32/int64 三种宽度）
       再对所有字段求"一致赋值"（每个字段取一个候选偏移，各字段偏移互不重叠）
  5. 输出解出的布局，并按它 dump 全部字段

用法：
  # 反解：给出类名、实例地址、已知字段值
  python hybridclr_layout.py solve Sv2Warchess.Sv2WarchessBattlePlayerBase 0x1f896f953f0 \
      --know life=24 --know grade=6 --know hero_id=1106 --uid 907732999798

  # 直接用已知布局读全部字段
  python hybridclr_layout.py dump <类名> <地址> --layout decl

  # 列出某类的字段
  python hybridclr_layout.py fields Sv2WarchessBattlePlayerBase
"""
import argparse
import itertools
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from shadow_warchess_reader import find_game_pid, open_proc, read  # noqa: E402

TYPES_TXT = os.path.join(HERE, "_types_warchess.txt")
HEADER = 0x10          # 对象头：klass + monitor

# .NET 类型的尺寸（值类型按实际，引用类型一律 8 字节指针）
PRIM = {
    "bool": 1, "byte": 1, "sbyte": 1, "char": 2,
    "short": 2, "ushort": 2, "int": 4, "uint": 4,
    "long": 8, "ulong": 8, "float": 4, "double": 8,
}


def type_size(t):
    """把元数据里的类型串转成字节尺寸；引用类型返回 8"""
    t = t.strip()
    if t in PRIM:
        return PRIM[t]
    if t.endswith("[]"):          # 数组 = 引用
        return 8
    if t.startswith("System."):
        base = t.split(".")[-1]
        if base in PRIM:
            return PRIM[base]
        return 8                  # String / Object / Nullable`1 等都当引用
    return 8                      # 自定义类/结构体/泛型 → 按引用处理


def load_classes():
    """
    解析 `_types_warchess.txt`，返回
      {类全名: [{name, type, size}, ...]}
    格式形如：
      ### Sv2Warchess.Sv2WarchessBattlePlayerBase  (23f/50m)
        F long <UserId>k__BackingField
    """
    out = {}
    cur = None
    if not os.path.isfile(TYPES_TXT):
        raise SystemExit(f"找不到元数据表：{TYPES_TXT}")
    with open(TYPES_TXT, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^### (\S+)\s+\(", line)
            if m:
                cur = m.group(1)
                out[cur] = []
                continue
            m = re.match(r"^\s+F (\S+)\s+(\S+)", line)
            if m and cur:
                t, n = m.group(1), m.group(2)
                out[cur].append({"name": n, "type": t, "size": type_size(t)})
    return out


def find_class_by_short(classes, short):
    for k in classes:
        if k == short or k.split(".")[-1] == short:
            return k
    return None


# ---------------------------------------------------------------- 布局假设
def layout_decl(fields, header=HEADER):
    """声明顺序 + 自然对齐（IL2CPP 对普通类的常见规则）"""
    off, out = header, {}
    for f in fields:
        sz = f["size"]
        if off % sz:
            off += sz - off % sz
        out[f["name"]] = off
        off += sz
    return out, off


def layout_width(fields, header=HEADER):
    """按宽度降序排（CoreCLR AutoLayout 的经典做法）"""
    off, out = header, {}
    for size in (8, 4, 2, 1):
        for f in fields:
            if f["size"] != size:
                continue
            if off % size:
                off += size - off % size
            out[f["name"]] = off
            off += size
    return out, off


def layout_slots(fields, header=HEADER):
    """每个字段独占一个 8 字节槽（HybridCLR 解释器可能的布局）"""
    off, out = header, {}
    for f in fields:
        out[f["name"]] = off
        off += 8
    return out, off


def layout_packed(fields, header=HEADER):
    """紧凑打包，不对齐"""
    off, out = header, {}
    for f in fields:
        out[f["name"]] = off
        off += f["size"]
    return out, off


LAYOUTS = {"decl": layout_decl, "width": layout_width,
           "slots": layout_slots, "packed": layout_packed}


# ---------------------------------------------------------------- 取值
def read_field(h, addr, off, size):
    d = read(h, addr + off, 8)
    if not d or len(d) < 8:
        return None
    if size == 1:
        return struct.unpack_from("<b", d, 0)[0]
    if size == 2:
        return struct.unpack_from("<h", d, 0)[0]
    if size == 4:
        return struct.unpack_from("<i", d, 0)[0]
    if size == 8:
        return struct.unpack_from("<q", d, 0)[0]
    return None


def startswith_ascii(name):
    return name.startswith("<") and name.endswith(">k__BackingField")


def clean(name):
    return name.replace("<", "").replace(">k__BackingField", "")


def norm(name):
    """
    字段名归一化，用于把用户写的名字和元数据里的名字对上。
    ⚠️ 必须去掉下划线并统一小写：用户会写 `hero_id`，元数据里是 `HeroId`，
       之前直接小写比较→ `hero_id` != `heroid`，这个字段永远匹配不上
       （自测里就因此只解出 2 个字段）。
    """
    return clean(name).replace("_", "").lower()


# ---------------------------------------------------------------- 验证
def verify_layout(h, addr, fields, layout, know):
    """用已知值检验一个布局假设；返回 (命中数, 总数, 明细)"""
    hits, detail = 0, []
    for kname, kval in know.items():
        cand = [f for f in fields if norm(f["name"]) == norm(kname)]
        if not cand:
            continue
        f = cand[0]
        off = layout.get(f["name"])
        if off is None:
            continue
        v = read_field(h, addr, off, f["size"])
        ok = (v == kval)
        detail.append((kname, off, f["size"], v, kval, ok))
        if ok:
            hits += 1
    return hits, len(know), detail


def brute_force(h, addr, fields, know, span=0x300):
    """
    暴力反解：为每个已知字段找出内存里所有匹配值的位置，
    再求一组"互不重叠、类型宽度一致"的赋值。

    返回 (layout, 明细) 或 (None, 说明)
    """
    raw = read(h, addr + HEADER, span)
    if not raw:
        return None, "读取失败"

    def occurrences(val):
        out = []
        for off in range(0, len(raw) - 8 + 1):
            if struct.unpack_from("<i", raw, off)[0] == val:
                out.append(("int32", HEADER + off))
            if struct.unpack_from("<q", raw, off)[0] == val:
                out.append(("int64", HEADER + off))
            if off + 2 <= len(raw) and struct.unpack_from("<h", raw, off)[0] == val:
                out.append(("int16", HEADER + off))
        return out

    per_field = {}
    for kname, kval in know.items():
        occ = occurrences(kval)
        if not occ:
            return None, f"字段 {kname}={kval} 在实例里找不到"
        per_field[kname] = occ

    # 深度优先找一组互不重叠的赋值
    names = list(per_field.keys())
    result = {}

    def dfs(i, used):
        if i == len(names):
            return True
        n = names[i]
        for typ, off in per_field[n]:
            # 简单约束：不同字段的偏移不能相同
            if off in used:
                continue
            result[n] = (typ, off)
            if dfs(i + 1, used | {off}):
                return True
            del result[n]
        return False

    if not dfs(0, set()):
        return None, "找不到互不冲突的偏移组合（可能字段值有重复或实例不对）"

    # 把解映射回字段名
    layout = {}
    detail = []
    for n, (typ, off) in result.items():
        cand = [f for f in fields if norm(f["name"]) == norm(n)]
        if cand:
            layout[cand[0]["name"]] = off
            detail.append((n, off, typ, know[n]))
    return layout, detail


# ---------------------------------------------------------------- 命令
def cmd_fields(args):
    classes = load_classes()
    key = find_class_by_short(classes, args.cls)
    if not key:
        print(f"找不到类 {args.cls}")
        return 1
    fs = classes[key]
    print(f"{key}  共 {len(fs)} 个字段")
    for i, f in enumerate(fs):
        print(f"  [{i:2d}] +?  {f['type']:<44} {f['name']:<40} size={f['size']}")
    return 0


def cmd_solve(args):
    classes = load_classes()
    key = find_class_by_short(classes, args.cls)
    if not key:
        print(f"找不到类 {args.cls}")
        return 1
    fields = classes[key]
    pid = args.pid or find_game_pid()[0]
    if not pid:
        print("找不到游戏进程")
        return 1
    h = open_proc(pid)
    addr = int(args.addr, 0)

    know = {}
    for kv in args.know or []:
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        know[k.strip()] = int(v, 0)

    print(f"类 {key}  实例 {hex(addr)}  已知值 {know}")
    print("")
    best = None
    for name, fn in LAYOUTS.items():
        layout, total = fn(fields)
        hits, tot, detail = verify_layout(h, addr, fields, layout, know)
        mark = "✅" if hits == len(know) and know else ""
        print(f"  {name:<7} 字段区 {total-HEADER:#x}  命中 {hits}/{len(know)} {mark}")
        for kname, off, sz, got, want, ok in detail:
            print(f"      {kname:<12} +{off:#04x} sz{sz}  读到 {got!s:<14} 期望 {want}  {'✓' if ok else '✗'}")
        if know and hits == len(know):
            best = (name, layout)
    print("")

    if best:
        print(f"==> 布局为 {best[0]}，可直接用 --layout {best[0]} 读取")
        layout = best[1]
    else:
        print("==> 四种假设都不匹配，进入**暴力反解**")
        layout, detail = brute_force(h, addr, fields, know)
        if layout is None:
            print(f"    失败：{detail}")
            return 2
        print("    解出：")
        for n, off, typ, val in detail:
            print(f"      {n:<12} +{off:#04x} ({typ}) = {val}")
        print("    其余字段按此布局的相对位置推断（见 dump）")

    # dump
    print("")
    print("=== 按解出的布局 dump ===")
    for f in fields:
        off = layout.get(f["name"])
        if off is None:
            print(f"  +??  {f['type']:<40} {f['name']:<34} = (未知偏移)")
            continue
        v = read_field(h, addr, off, f["size"])
        print(f"  +{off:#04x}  {f['type']:<40} {f['name']:<34} = {v}")
    return 0


def cmd_dump(args):
    classes = load_classes()
    key = find_class_by_short(classes, args.cls)
    if not key:
        print(f"找不到类 {args.cls}")
        return 1
    fields = classes[key]
    layout, total = LAYOUTS[args.layout](fields)
    pid = args.pid or find_game_pid()[0]
    h = open_proc(pid)
    addr = int(args.addr, 0)
    for f in fields:
        off = layout[f["name"]]
        v = read_field(h, addr, off, f["size"])
        print(f"  +{off:#04x}  {f['type']:<40} {f['name']:<34} = {v}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("fields", help="列出某类的字段")
    p1.add_argument("cls")
    p1.set_defaults(func=cmd_fields)

    p2 = sub.add_parser("solve", help="用已知值反解布局")
    p2.add_argument("cls")
    p2.add_argument("addr")
    p2.add_argument("--know", action="append", help="字段=值，可多次给")
    p2.set_defaults(func=cmd_solve)

    p3 = sub.add_parser("dump", help="按指定布局 dump")
    p3.add_argument("cls")
    p3.add_argument("addr")
    p3.add_argument("--layout", default="decl", choices=list(LAYOUTS))
    p3.set_defaults(func=cmd_dump)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

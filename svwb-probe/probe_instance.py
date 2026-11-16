"""Phase 3: score candidate instances of a HybridCLR type and decode known fields.

Usage: probe_instance.py <pid> [typeName] [namespace]
Default target: Netease.GameNet.MOS_PROTO.S_COMBAT_OPPONENT

Read-only. Field offsets are *predicted* (HybridCLR lays interpreter fields out in
declaration order with natural alignment after the 16-byte Il2CppObject header);
the scoring below only needs the prediction to be roughly right to reject noise.
"""
import ctypes as C
import struct, sys, time
import numpy as np
sys.path.insert(0, r"C:\Users\33949\Documents\deepseek-harness\default-workspace\svwb-probe")
from probe_mem import k32, regions, read, module_base, PROCESS_QUERY_INFORMATION, PROCESS_VM_READ
from probe_scan import scan_bytes, scan_for_values, cstr


def open_proc(pid):
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        raise SystemExit("OpenProcess failed %d" % C.get_last_error())
    return h


def q(h, a):
    d = read(h, a, 8)
    return struct.unpack("<Q", d)[0] if d else None


def i64(h, a):
    d = read(h, a, 8)
    return struct.unpack("<q", d)[0] if d else None


def i32(h, a):
    d = read(h, a, 4)
    return struct.unpack("<i", d)[0] if d else None


def i16(h, a):
    d = read(h, a, 2)
    return struct.unpack("<h", d)[0] if d else None


def find_class(h, tname, nsname):
    hits = scan_bytes(h, tname.encode() + b"\x00", limit=8)
    slots = scan_for_values(h, set(hits))
    for slot, val in slots:
        cand = slot - 0x10
        nsp = q(h, cand + 0x18)
        if nsp:
            s = cstr(h, nsp, 64)
            if s and nsname[:8] in s:
                return cand, hits, slots
    return None, hits, slots


# predicted layout: name -> (offset, kind)
LAYOUT_COMBAT = [
    ("uid", 0x10, "i64"), ("hero_id", 0x18, "i32"), ("life", 0x20, "i64"),
    ("armor", 0x28, "i64"), ("grade", 0x30, "i16"), ("random_num", 0x34, "i32"),
    ("minions", 0x38, "ptr"), ("evolve_point", 0x40, "i32"),
    ("cards_num", 0x44, "i32"), ("self_minions", 0x48, "ptr"),
    ("self_cards", 0x50, "ptr"), ("skills", 0x58, "ptr"), ("skill_cost", 0x60, "i32"),
    ("is_priviliged", 0x64, "i32"), ("double_skill_id", 0x7C, "i32"),
]


def score_combat(h, a):
    v = {}
    for n, o, k in LAYOUT_COMBAT:
        if k == "i64":
            v[n] = i64(h, a + o)
        elif k == "i32":
            v[n] = i32(h, a + o)
        elif k == "i16":
            v[n] = i16(h, a + o)
        else:
            v[n] = q(h, a + o)
    s = 0
    if v["uid"] and 10**8 < v["uid"] < 10**12:
        s += 3
    if v["hero_id"] and 1 <= v["hero_id"] <= 10000:
        s += 2
    if v["life"] is not None and -10 <= v["life"] <= 100:
        s += 2
    if v["armor"] is not None and 0 <= v["armor"] <= 100:
        s += 1
    if v["grade"] is not None and 1 <= v["grade"] <= 12:
        s += 3
    if v["evolve_point"] is not None and 0 <= v["evolve_point"] <= 20:
        s += 1
    if v["cards_num"] is not None and 0 <= v["cards_num"] <= 20:
        s += 1
    if v["minions"] and v["minions"] > 0x10000:
        s += 1
    return s, v


def main():
    pid = int(sys.argv[1])
    tname = sys.argv[2] if len(sys.argv) > 2 else "S_COMBAT_OPPONENT"
    nsname = sys.argv[3] if len(sys.argv) > 3 else "Netease.GameNet.MOS_PROTO"
    h = open_proc(pid)
    cb, hits, slots = find_class(h, tname, nsname)
    print(f"type={nsname}.{tname} class=0x{cb:X}" if cb else "class NOT found")
    if not cb:
        return
    t0 = time.time()
    cand = scan_for_values(h, {cb})
    print(f"raw pointer-to-class sites: {len(cand)} ({time.time()-t0:.1f}s)")
    scored = []
    for addr, _ in cand:
        s, v = score_combat(h, addr)
        scored.append((s, addr, v))
    scored.sort(key=lambda x: -x[0])
    print("\n top candidates by heuristic score:")
    for s, addr, v in scored[:10]:
        grade = v["grade"]
        print(f"  score={s:2d} @0x{addr:X}  " + " ".join(
            f"{k}={v[k]}" for k in ("uid", "hero_id", "life", "armor", "grade", "cards_num", "minions")))


if __name__ == "__main__":
    main()

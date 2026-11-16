"""Phase 4: raw candidate dump for a HybridCLR type (read-only).

Prints every site whose 8-byte value equals the type's Il2CppClass pointer,
with the first 0x60 bytes decoded as 8 qwords + a guess of uid-like values.
Purpose: eyeball the real object layout instead of trusting the predicted one.
"""
import struct, sys, time
import numpy as np
sys.path.insert(0, r"C:\Users\33949\Documents\deepseek-harness\default-workspace\svwb-probe")
from probe_mem import k32, read, PROCESS_QUERY_INFORMATION, PROCESS_VM_READ
from probe_scan import scan_bytes, scan_for_values, cstr


def main():
    pid = int(sys.argv[1])
    tname = sys.argv[2]
    nsname = sys.argv[3] if len(sys.argv) > 3 else ""
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    hits = scan_bytes(h, tname.encode() + b"\x00", limit=8)
    print(f"[{tname}] name-string hits={len(hits)}")
    if not hits:
        return
    slots = scan_for_values(h, set(hits))
    cb = None
    for slot, val in slots:
        cand = slot - 0x10
        nsp = struct.unpack("<Q", read(h, cand + 0x18, 8))[0]
        s = cstr(h, nsp, 64) if nsp else None
        print(f"   class_cand=0x{cand:X} ns={s!r}")
        if s and (not nsname or nsname in s):
            cb = cand
    if cb is None:
        print("no class"); return
    print(f"class=0x{cb:X}")
    sites = scan_for_values(h, {cb})
    print(f"sites={len(sites)}")
    rows = []
    for addr, _ in sites:
        d = read(h, addr, 0x60)
        if not d or len(d) < 0x60:
            continue
        qs = struct.unpack("<12Q", d)
        uidish = sum(1 for x in qs if 10**8 < x < 10**13)
        small = sum(1 for x in qs if 0 <= x < 64)
        rows.append((uidish * 2 + small, addr, qs))
    rows.sort(key=lambda r: -r[0])
    for sc, addr, qs in rows[:6]:
        print(f"  @0x{addr:X} score={sc}")
        for i, x in enumerate(qs):
            print(f"     +0x{i*8:02X} = 0x{x:016X} ({x})")


if __name__ == "__main__":
    main()

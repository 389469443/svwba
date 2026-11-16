"""Phase 2: locate the HybridCLR Il2CppClass for a hot-update type, then its instances.

Read-only. Strategy
  1. scan committed memory for the ASCII type name -> N hit(s) inside the
     loaded interpreter image (this is Il2CppClass::name target)
  2. scan for 8-byte values == hit address -> candidate Il2CppClass::name slots,
     so Il2CppClass base = slot - 0x10   (IL2CPP 2022 layout: image@0, gc_desc@8,
     name@0x10, namespaze@0x18)
  3. validate: namespaze must point to ASCII, image pointer must be plausible
  4. scan for 8-byte values == class base  -> candidate object instances
     (object layout: klass@0x00, monitor@0x08, first field @0x10)
  5. dump instance bytes + guessed field values
"""
import ctypes as C
import ctypes.wintypes as W
import sys, struct, time
import numpy as np

sys.path.insert(0, r"C:\Users\33949\Documents\deepseek-harness\default-workspace\svwb-probe")
from probe_mem import (k32, psapi, regions, read, module_base, PROCESS_QUERY_INFORMATION,
                       PROCESS_VM_READ, C as _c, W as _w)


def open_proc(pid):
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        raise SystemExit("OpenProcess failed %d" % C.get_last_error())
    return h


def cstr(h, addr, maxlen=96):
    d = read(h, addr, maxlen)
    if not d:
        return None
    z = d.find(b"\x00")
    if z < 0:
        z = len(d)
    try:
        return d[:z].decode("ascii")
    except UnicodeDecodeError:
        return None


def scan_for_values(h, targets, chunk=8 << 20, want_all=True):
    """return list of addresses where an 8-byte LE value equals any target."""
    tgt = np.array(sorted(targets), dtype=np.uint64)
    found = []
    regs = regions(h)
    for base, size, typ, prot in regs:
        off = 0
        while off < size:
            n = min(chunk, size - off)
            data = read(h, base + off, n)
            if data and len(data) >= 8:
                arr = np.frombuffer(data[:len(data) // 8 * 8], dtype="<u8")
                # np.isin on 6GB with small target set: use sorted search
                idx = np.isin(arr, tgt)
                if idx.any():
                    for i in np.nonzero(idx)[0]:
                        found.append((base + off + int(i) * 8, int(arr[i])))
            off += n
    return found


def scan_bytes(h, needle, chunk=8 << 20, limit=64):
    hits = []
    regs = regions(h)
    for base, size, typ, prot in regs:
        off = 0
        while off < size:
            n = min(chunk, size - off)
            data = read(h, base + off, n)
            if data:
                pos = 0
                while True:
                    i = data.find(needle, pos)
                    if i < 0:
                        break
                    hits.append(base + off + i)
                    pos = i + 1
                    if len(hits) >= limit:
                        return hits
            off += n
    return hits


def dump(h, addr, n=0x100):
    d = read(h, addr, n)
    if not d:
        return
    for i in range(0, len(d), 16):
        row = d[i:i + 16]
        hexs = " ".join("%02x" % b for b in row)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        print(f"    {addr + i:016X}  {hexs:<48}  {asc}")


def main():
    pid = int(sys.argv[1])
    tname = sys.argv[2] if len(sys.argv) > 2 else "S_COMBAT_OPPONENT"
    nsname = sys.argv[3] if len(sys.argv) > 3 else "Netease.GameNet.MOS_PROTO"
    h = open_proc(pid)
    hmod, path = module_base(h, "GameAssembly.dll")
    print(f"pid={pid} GameAssembly=0x{hmod:X}")

    t0 = time.time()
    hits = scan_bytes(h, tname.encode() + b"\x00", limit=8)
    print(f"[1] type-name string hits: {len(hits)}  ({time.time()-t0:.1f}s)")
    for a in hits:
        print(f"    0x{a:X}")

    t0 = time.time()
    slots = scan_for_values(h, set(hits))
    print(f"[2] pointers to name string: {len(slots)}  ({time.time()-t0:.1f}s)")

    classes = []
    for slot, val in slots:
        cand = slot - 0x10
        img = read(h, cand, 8)
        ns = read(h, cand + 0x18, 8)
        nsp = struct.unpack("<Q", ns)[0] if ns else 0
        nsstr = cstr(h, nsp, 64) if nsp else None
        ok = nsstr is not None and nsname[:8] in (nsstr or "")
        print(f"    slot=0x{slot:X} -> class_base=0x{cand:X} namespaze=0x{nsp:X} {nsstr!r} {'OK' if ok else ''}")
        if ok:
            classes.append(cand)
    if not classes:
        print("no validated class found; dumping first candidate header:")
        if slots:
            dump(h, slots[0][0] - 0x40, 0x100)
        return

    for cb in classes:
        print(f"\n[3] class 0x{cb:X} header:")
        dump(h, cb, 0x120)
        t0 = time.time()
        inst = scan_for_values(h, {cb})
        print(f"[4] instances (klass ptr == class): {len(inst)}  ({time.time()-t0:.1f}s)")
        for addr, _ in inst[:8]:
            print(f"  instance @0x{addr:X}")
            dump(h, addr, 0x80)


if __name__ == "__main__":
    main()

"""Read-only process memory probe for 影之诗幻境 (IL2CPP / HybridCLR).

STRICTLY READ-ONLY:
  * OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ)
  * VirtualQueryEx + ReadProcessMemory
  * never WriteProcessMemory, never CreateRemoteThread, never inject

Step 1 (this file, --phase plumb):
  - find pid, module base of GameAssembly.dll, verify MZ
  - scan committed readable regions for an anchor ASCII string
    (default: b"S_COMBAT_OPPONENT" - a HybridCLR type name that must exist
     in the loaded interpreter image)
  - report hit count + addresses, proving heap scanning works
"""
import ctypes as C
import ctypes.wintypes as W
import sys, struct, time

k32 = C.WinDLL("kernel32", use_last_error=True)
psapi = C.WinDLL("psapi", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_NOACCESS = 0x01
PAGE_GUARD = 0x100
MEM_PRIVATE = 0x20000
MEM_IMAGE = 0x1000000

class MEMORY_BASIC_INFORMATION(C.Structure):
    _fields_ = [("BaseAddress", C.c_void_p), ("AllocationBase", C.c_void_p),
                ("AllocationProtect", W.DWORD), ("RegionSize", C.c_size_t),
                ("State", W.DWORD), ("Protect", W.DWORD), ("Type", W.DWORD)]

k32.OpenProcess.restype = W.HANDLE
k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k32.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MEMORY_BASIC_INFORMATION), C.c_size_t]
k32.VirtualQueryEx.restype = C.c_size_t
k32.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)]
k32.ReadProcessMemory.restype = W.BOOL


def find_pid(name_sub="MuMu模拟器x影之诗高清版"):
    import subprocess
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True).stdout
    for line in out.decode("mbcs", "replace").splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and name_sub in parts[0]:
            return int(parts[1]), parts[0]
    return None, None


def regions(h):
    out = []
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION()
    while k32.VirtualQueryEx(h, C.c_void_p(addr), C.byref(mbi), C.sizeof(mbi)):
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize or 0
        if size == 0:
            break
        if (mbi.State == MEM_COMMIT and not (mbi.Protect & PAGE_NOACCESS)
                and not (mbi.Protect & PAGE_GUARD)):
            out.append((base, size, mbi.Type, mbi.Protect))
        addr = base + size
        if addr > 0x7FFFFFFFFFFF:
            break
    return out


def read(h, addr, size):
    buf = (C.c_char * size)()
    got = C.c_size_t(0)
    if k32.ReadProcessMemory(h, C.c_void_p(addr), buf, size, C.byref(got)) and got.value:
        return buf.raw[:got.value]
    return None


def module_base(h, name_sub):
    mods = (W.HMODULE * 2048)()
    needed = W.DWORD(0)
    psapi.EnumProcessModulesEx.argtypes = [W.HANDLE, C.POINTER(W.HMODULE), W.DWORD, C.POINTER(W.DWORD), W.DWORD]
    psapi.EnumProcessModulesEx.restype = W.BOOL
    psapi.GetModuleFileNameExW.argtypes = [W.HANDLE, W.HMODULE, W.LPWSTR, W.DWORD]
    psapi.GetModuleFileNameExW.restype = W.DWORD
    if not psapi.EnumProcessModulesEx(h, mods, C.sizeof(mods), C.byref(needed), 0x03):
        print("EnumProcessModulesEx failed", C.get_last_error())
        return None, None
    n = needed.value // C.sizeof(W.HMODULE)
    for i in range(n):
        hmod = mods[i]
        buf = C.create_unicode_buffer(1024)
        psapi.GetModuleFileNameExW(h, hmod, buf, 1024)
        if name_sub.lower() in buf.value.lower():
            return int(hmod), buf.value
    return None, None


def main():
    args = [a for a in sys.argv[1:]]
    pid = None
    if args and args[0].isdigit():
        pid = int(args.pop(0))
    anchor = args[0].encode() if args else b"S_COMBAT_OPPONENT"
    pname = "?"
    if pid is None:
        pid, pname = find_pid()
    if not pid:
        print("game process not found"); return 1
    print(f"pid={pid} name={pname}")
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        print("OpenProcess failed err=", C.get_last_error()); return 2
    print("handle ok", h)
    hmod, path = module_base(h, "GameAssembly.dll")
    if hmod:
        base = hmod
        hdr = read(h, base, 0x400)
        print(f"GameAssembly.dll base=0x{base:X} path={path}")
        print("  header:", hdr[:2], "e_lfanew=", struct.unpack_from("<I", hdr, 0x3c)[0] if hdr else None)
    regs = regions(h)
    tot = sum(s for _, s, _, _ in regs)
    print(f"regions={len(regs)} committed_readable={tot/1e6:.1f} MB")

    t0 = time.time()
    hits = []
    scanned = 0
    CHUNK = 8 << 20
    for base, size, typ, prot in regs:
        off = 0
        while off < size:
            n = min(CHUNK, size - off)
            data = read(h, base + off, n)
            if data:
                scanned += len(data)
                pos = 0
                while True:
                    i = data.find(anchor, pos)
                    if i < 0:
                        break
                    hits.append(base + off + i)
                    pos = i + 1
                    if len(hits) > 64:
                        break
            off += n
        if len(hits) > 64:
            break
    dt = time.time() - t0
    print(f"scanned {scanned/1e6:.1f} MB in {dt:.1f}s, anchor hits={len(hits)}")
    for a in hits[:20]:
        print(f"   hit @0x{a:X}")
    print("result:", "OK - RPM + heap scan works" if hits else "no hit (check anchor / permissions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

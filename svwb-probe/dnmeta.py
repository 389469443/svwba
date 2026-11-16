"""Minimal .NET (ECMA-335) metadata reader for HybridCLR hot-update assemblies.

Handles assemblies whose PE header is obfuscated but whose metadata root (BSJB)
and tables (#~) are still plaintext -- the case for sv2Warchess.bytes.
Pure read-only parsing, no external deps.
"""
import struct, sys, os

# ---- coded index definitions: name -> (tables, tag_bits) -------------------
CODED = {
    "TypeDefOrRef": ([2, 1, 27], 2),
    "HasConstant": ([4, 8, 23], 2),
    "HasCustomAttribute": ([6, 4, 1, 2, 8, 9, 10, 0, 14, 23, 20, 17, 26, 27, 32, 35, 38, 39, 40, 42, 44, 43], 5),
    "HasFieldMarshal": ([4, 8], 1),
    "HasDeclSecurity": ([2, 6, 32], 2),
    "MemberRefParent": ([2, 1, 26, 6, 27], 3),
    "HasSemantics": ([20, 23], 1),
    "MethodDefOrRef": ([6, 10], 1),
    "MemberForwarded": ([4, 6], 1),
    "Implementation": ([38, 35, 39], 2),
    "CustomAttributeType": ([6, 10], 3),
    "ResolutionScope": ([0, 26, 35, 1], 2),
    "TypeOrMethodDef": ([2, 6], 1),
}

# ---- table schemas (ECMA-335 II.22) ---------------------------------------
# column spec: int byte width, 'S' string, 'G' guid, 'B' blob,
#              ('T', n) simple table index, ('C', name) coded index
S, G, B = "S", "G", "B"
def T(n): return ("T", n)
def C(n): return ("C", n)

TABLES = [
    ("Module", [2, S, G, G, G]),
    ("TypeRef", [C("ResolutionScope"), S, S]),
    ("TypeDef", [4, S, S, C("TypeDefOrRef"), T(4), T(6)]),
    ("FieldPtr", [T(4)]),
    ("Field", [2, S, B]),
    ("MethodPtr", [T(6)]),
    ("MethodDef", [4, 2, 2, S, B, T(8)]),
    ("ParamPtr", [T(8)]),
    ("Param", [2, 2, S]),
    ("InterfaceImpl", [T(2), C("TypeDefOrRef")]),
    ("MemberRef", [C("MemberRefParent"), S, B]),
    ("Constant", [1, 1, C("HasConstant"), B]),
    ("CustomAttribute", [C("HasCustomAttribute"), C("CustomAttributeType"), B]),
    ("FieldMarshal", [C("HasFieldMarshal"), B]),
    ("DeclSecurity", [2, C("HasDeclSecurity"), B]),
    ("ClassLayout", [2, 4, T(2)]),
    ("FieldLayout", [4, T(4)]),
    ("StandAloneSig", [B]),
    ("EventMap", [T(2), T(20)]),
    ("EventPtr", [T(20)]),
    ("Event", [2, S, C("TypeDefOrRef")]),
    ("PropertyMap", [T(2), T(23)]),
    ("PropertyPtr", [T(23)]),
    ("Property", [2, S, B]),
    ("MethodSemantics", [2, T(6), C("HasSemantics")]),
    ("MethodImpl", [T(2), C("MethodDefOrRef"), C("MethodDefOrRef")]),
    ("ModuleRef", [S]),
    ("TypeSpec", [B]),
    ("ImplMap", [2, C("MemberForwarded"), S, T(26)]),
    ("FieldRVA", [4, T(4)]),
    ("ENCLog", [4, 4]),
    ("ENCMap", [4]),
    ("Assembly", [4, 2, 2, 2, 2, 4, B, S, S]),
    ("AssemblyProcessor", [4]),
    ("AssemblyOS", [4, 4, 4]),
    ("AssemblyRef", [2, 2, 2, 2, 4, B, S, S, B]),
    ("AssemblyRefProcessor", [4, T(35)]),
    ("AssemblyRefOS", [4, 4, 4, T(35)]),
    ("File", [4, S, B]),
    ("ExportedType", [4, 4, S, S, C("Implementation")]),
    ("ManifestResource", [4, 4, S, C("Implementation")]),
    ("NestedClass", [T(2), T(2)]),
    ("GenericParam", [2, 2, C("TypeOrMethodDef"), S]),
    ("MethodSpec", [C("MethodDefOrRef"), B]),
    ("GenericParamConstraint", [T(42), C("TypeDefOrRef")]),
]

# ---- element types (ECMA-335 II.23.1.16) ---------------------------------
PRIM = {0x01: "void", 0x02: "bool", 0x03: "char", 0x04: "sbyte", 0x05: "byte",
        0x06: "short", 0x07: "ushort", 0x08: "int", 0x09: "uint", 0x0a: "long",
        0x0b: "ulong", 0x0c: "float", 0x0d: "double", 0x0e: "string",
        0x16: "TypedRef", 0x18: "IntPtr", 0x19: "UIntPtr", 0x1c: "object"}


class Reader:
    def __init__(self, data, pos):
        self.d, self.p = data, pos
    def u8(self):
        v = self.d[self.p]; self.p += 1; return v
    def u16(self):
        v = struct.unpack_from("<H", self.d, self.p)[0]; self.p += 2; return v
    def u32(self):
        v = struct.unpack_from("<I", self.d, self.p)[0]; self.p += 4; return v


class NetMeta:
    def __init__(self, data):
        self.d = data
        i = data.find(b"BSJB")
        if i < 0:
            raise ValueError("no BSJB metadata root")
        self.root = i
        r = Reader(data, i)
        self.sig = r.u32(); self.major = r.u16(); self.minor = r.u16()
        r.u32()  # reserved
        vlen = r.u32()
        self.version = data[r.p:r.p + vlen].split(b"\x00")[0].decode("latin1")
        r.p += vlen
        r.p += (-r.p) % 4  # pad to 4
        r.u16(); nstreams = r.u16()
        self.streams = {}
        for _ in range(nstreams):
            off, size = r.u32(), r.u32()
            name = bytearray()
            while True:
                ch = data[r.p]; r.p += 1
                if ch == 0:
                    break
                name.append(ch)
            r.p += (-r.p) % 4
            self.streams[name.decode("latin1")] = (off, size)
        self.tables_off = i + self.streams["#~"][0]
        self.tables_size = self.streams["#~"][1]
        self.strings_off = i + self.streams["#Strings"][0]
        self.blob_off = i + self.streams["#Blob"][0]
        self._parse_tables()
        self._parse_strings()

    # ---------------- strings ----------------
    def _parse_strings(self):
        off, size = self.streams["#Strings"]
        base = self.root + off
        self.strbase = base
        self.strsize = size

    def s(self, idx):
        if idx == 0:
            return ""
        b = self.strbase + idx
        e = self.d.find(b"\x00", b)
        return self.d[b:e].decode("utf-8", "replace")

    def blob(self, idx):
        b = self.root + self.streams["#Blob"][0] + idx
        d = self.d
        x = d[b]
        if x & 0x80 == 0:
            ln, p = x, b + 1
        elif x & 0xC0 == 0x80:
            ln, p = ((x & 0x3F) << 8) | d[b + 1], b + 2
        else:
            ln, p = ((x & 0x1F) << 24) | (d[b + 1] << 16) | (d[b + 2] << 8) | d[b + 3], b + 4
        return d[p:p + ln]

    # ---------------- tables ----------------
    def _parse_tables(self):
        d = self.d
        r = Reader(d, self.tables_off)
        r.u32(); r.u8(); r.u8()
        heap = r.u8(); r.u8()
        self.str_wide = bool(heap & 1)
        self.guid_wide = bool(heap & 2)
        self.blob_wide = bool(heap & 4)
        valid = struct.unpack_from("<Q", d, r.p)[0]; r.p += 8
        r.p += 8  # sorted
        self.rows = {}
        for t in range(64):
            if valid >> t & 1:
                self.rows[t] = r.u32()
        self.pos = {}
        p = r.p
        for t in range(len(TABLES)):
            if t not in self.rows:
                continue
            self.pos[t] = p
            p += self._row_size(t) * self.rows[t]
        self.tables_end = p
        self.tables_ok = (p - self.tables_off) <= self.tables_size

    def _col_size(self, col):
        if isinstance(col, int):
            return col
        if col == S:
            return 4 if self.str_wide else 2
        if col == G:
            return 4 if self.guid_wide else 2
        if col == B:
            return 4 if self.blob_wide else 2
        if col[0] == "T":
            return 4 if self.rows.get(col[1], 0) > 0xFFFF else 2
        if col[0] == "C":
            tabs, bits = CODED[col[1]]
            mx = max(self.rows.get(t, 0) for t in tabs)
            return 4 if mx >= (1 << (16 - bits)) else 2
        raise ValueError(col)

    def _row_size(self, t):
        return sum(self._col_size(c) for c in TABLES[t][1])

    def row(self, t, idx):
        """idx is 1-based."""
        p = self.pos[t] + (idx - 1) * self._row_size(t)
        out = {}
        for ci, col in enumerate(TABLES[t][1]):
            sz = self._col_size(col)
            if sz == 1:
                v = self.d[p]; p += 1
            elif sz == 2:
                v = struct.unpack_from("<H", self.d, p)[0]; p += 2
            else:
                v = struct.unpack_from("<I", self.d, p)[0]; p += 4
            out[ci] = v
        return out

    # ---------------- high level ----------------
    def types(self):
        """yield dict(name, ns, fields[], methods[], nfields, nmethods)"""
        nT = self.rows.get(2, 0)
        nF = self.rows.get(4, 0)
        nM = self.rows.get(6, 0)
        trs = [self.row(2, i) for i in range(1, nT + 1)]
        for i, tr in enumerate(trs):
            fstart = tr[4] or (nF + 1)
            fend = trs[i + 1][4] if i + 1 < len(trs) else nF + 1
            mstart = tr[5] or (nM + 1)
            mend = trs[i + 1][5] if i + 1 < len(trs) else nM + 1
            fields = []
            for fi in range(fstart, fend):
                if fi < 1 or fi > nF:
                    continue
                fr = self.row(4, fi)
                fields.append((self.s(fr[1]), self.parse_type(fr[2]), fr[0]))
            methods = []
            for mi in range(mstart, mend):
                if mi < 1 or mi > nM:
                    continue
                mr = self.row(6, mi)
                methods.append(self.s(mr[3]))
            yield {"name": self.s(tr[1]), "ns": self.s(tr[2]), "fields": fields,
                   "methods": methods, "ext": tr[3]}

    def parse_type(self, blobidx, depth=0):
        b = self.blob(blobidx)
        if not b:
            return "?"
        p = 0
        if b[0] == 0x06:            # FIELD signature prefix
            p = 1
        elif b[0] == 0x08:          # PROPERTY: 08 [HASTHIS] paramcount ...
            p = 1
            if p < len(b) and b[p] & 0x20:
                p += 1
            x = b[p]; p += 1
            if x & 0x80 == 0:
                pass
            elif x & 0xC0 == 0x80:
                p += 1
            else:
                p += 3
        return self._et(b, p, depth)[0]

    def tname_typedef(self, rid):
        if rid < 1 or rid > self.rows.get(2, 0):
            return f"TypeDef#{rid}"
        r = self.row(2, rid)
        ns, nm = self.s(r[2]), self.s(r[1])
        return (ns + "." + nm) if ns else nm

    def tname_typeref(self, rid):
        if rid < 1 or rid > self.rows.get(1, 0):
            return f"TypeRef#{rid}"
        r = self.row(1, rid)
        ns, nm = self.s(r[2]), self.s(r[1])
        return (ns + "." + nm) if ns else nm

    def _et(self, b, p, depth=0):
        if p >= len(b):
            return "?", p
        et = b[p]; p += 1
        if et in PRIM:
            return PRIM[et], p
        if et == 0x0F:  # PTR
            t, p = self._et(b, p, depth + 1); return t + "*", p
        if et == 0x10:  # BYREF
            t, p = self._et(b, p, depth + 1); return "ref " + t, p
        if et in (0x11, 0x12):  # VALUETYPE / CLASS
            if p >= len(b):
                return "?", p
            x = b[p]
            if x & 0x80 == 0:
                comp, p = x, p + 1
            elif x & 0xC0 == 0x80:
                comp, p = ((x & 0x3F) << 8) | b[p + 1], p + 2
            else:
                comp, p = 0, p + 4
            tagt = (comp & 3)
            rid = comp >> 2
            if tagt == 0:
                return self.tname_typedef(rid), p
            if tagt == 1:
                return self.tname_typeref(rid), p
            return f"TypeSpec#{rid}", p
        if et == 0x1D:  # SZARRAY
            t, p = self._et(b, p, depth + 1); return t + "[]", p
        if et == 0x14:  # ARRAY
            t, p = self._et(b, p, depth + 1)
            p += 4
            n = self._cint(b, p); p = n[1]
            for _ in range(n[0]):
                q = self._cint(b, p); p = q[1]
                q = self._cint(b, p); p = q[1]
            return t + "[,]", p
        if et in (0x13, 0x1E):  # VAR / MVAR
            q = self._cint(b, p); return ("!T" if et == 0x13 else "!!T") + str(q[0]), q[1]
        if et == 0x15:  # GENERICINST
            t, p = self._et(b, p, depth + 1)
            n = self._cint(b, p); p = n[1]
            args = []
            for _ in range(n[0]):
                a, p = self._et(b, p, depth + 1)
                args.append(a)
            return f"{t}<{','.join(args)}>", p
        if et == 0x1B:  # FNPTR
            return "fnptr", p
        return f"et{et:#x}", p

    def _cint(self, b, p):
        x = b[p]; p += 1
        if x & 0x80 == 0:
            return x, p
        if x & 0xC0 == 0x80:
            return ((x & 0x3F) << 8) | b[p], p + 1
        return ((x & 0x1F) << 24) | (b[p] << 16) | (b[p + 1] << 8) | b[p + 2], p + 3


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        r"~\AppData\LocalLow\netease\MuMu模拟器x影之诗高清版\Hotfix\Windows\Release\sv2Warchess.bytes")
    data = open(p, "rb").read()
    m = NetMeta(data)
    print("metadata version:", m.version)
    print("streams:", {k: v for k, v in m.streams.items()})
    print("tables_off=%X end=%X size=%X ok=%s" % (m.tables_off, m.tables_end, m.tables_size, m.tables_ok))
    print("rows:", {TABLES[t][0]: n for t, n in sorted(m.rows.items())})
    print("total types:", m.rows.get(2, 0))

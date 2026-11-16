#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""影之诗纯只读读取器（不注入 hook、不卡游戏）。

思路（2026-11-08 主人要求）：
  1. 首次启动 / 游戏版本变化时，用 Frida **异步分块扫描** 找当前对局的
     BattleStatePlayerMpo 对象（按 vtable 特征），选中「turn 最大且数据在变」的那个；
  2. 之后每 250ms 用 ReadProcessMemory 纯读字段，写 logs/deck_live.json；
  3. 换局（数据不再变化 / turn 归零）→ 重新扫描一次。

用法：
  python readonly_reader.py            # 前台运行（调试用）
  pythonw -u readonly_reader.py        # 后台运行（控制台拉起）

环境变量：
  SVWB_READONLY_FAST=1  首次扫描后立刻开始输出（默认）
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
LIVE_PATH = os.path.join(LOGS, "deck_live.json")
OFFSETS_PATH = os.path.join(HERE, "gamedata", "readonly_offsets.json")

# Mpo 字段偏移（与 frida_live.py 一致）
OFF_HAND = 0x10
OFF_FIELD = 0x18
OFF_DECK = 0x20
OFF_LIFE = 0x24
OFF_PP = 0x34
OFF_TURN = 0x38
OFF_MULLIGAN = 0x5C
OFF_PLAYED = 0x70
OFF_DESTROYED = 0x78

# 上次探测得到的 vtable（游戏更新后会变，变了就重新扫）
DEFAULT_VTABLE = 0x1D6240C57B0

# Win32
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
PAGE_READABLE = (0x02, 0x04, 0x20, 0x40, 0x80)

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


kernel32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = (wt.HANDLE,)
kernel32.ReadProcessMemory.argtypes = (wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t))
kernel32.VirtualQueryEx.argtypes = (wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_size_t)

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


def open_process(pid):
    h = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    return h or None


def read_mem(handle, addr, size):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t(0)
    if not kernel32.ReadProcessMemory(handle, ctypes.c_void_p(addr), buf, size,
                                      ctypes.byref(got)):
        return None
    if got.value != size:
        return None
    return buf.raw


def read_i32(handle, addr):
    raw = read_mem(handle, addr, 4)
    if raw is None:
        return None
    return struct.unpack("<i", raw)[0]


def read_ptr(handle, addr):
    raw = read_mem(handle, addr, 8)
    if raw is None:
        return None
    return struct.unpack("<Q", raw)[0]


def readable_ranges(handle):
    """枚举可读内存区间。"""
    out = []
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION()
    while addr < 0x7FFFFFFFFFFF:
        r = kernel32.VirtualQueryEx(handle, ctypes.c_void_p(addr),
                                    ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not r:
            break
        base = mbi.BaseAddress or 0
        size = int(mbi.RegionSize or 0)
        if size <= 0:
            break
        # State=MEM_COMMIT 且权限可读
        if mbi.State == MEM_COMMIT and (mbi.Protect & 0xFF) in PAGE_READABLE:
            # 只保留私有内存（堆/栈），跳过映射文件（exe/dll/资源）——对象只在堆里
            if int(mbi.Type) == 0x20000:  # MEM_PRIVATE
                out.append((base, size))
        addr = base + size
        if len(out) > 4000:
            break
    return out


def _scan_one_range(handle, base, size, pattern, want):
    """扫描单个内存段（供线程池调用）。"""
    out = []
    off = 0
    CHUNK = 64 * 1024 * 1024
    while off < size and len(out) < want:
        chunk = min(CHUNK, size - off)
        raw = read_mem(handle, base + off, chunk)
        if raw:
            pos = 0
            while len(out) < want:
                i = raw.find(pattern, pos)
                if i < 0:
                    break
                addr = base + off + i
                deck = read_i32(handle, addr + OFF_DECK)
                life = read_i32(handle, addr + OFF_LIFE)
                if deck is not None and life is not None:
                    if 0 < deck <= 45 and 0 < life <= 30:
                        out.append({"addr": addr, "deck": deck, "life": life})
                pos = i + 8
        off += chunk
    return out


def scan_vtable_ctypes(handle, vtable, want=20, near=None):
    """纯外部多线程扫描（不注入、不卡游戏）。"""
    from concurrent.futures import ThreadPoolExecutor
    pattern = struct.pack("<Q", vtable)
    all_ranges = [r for r in readable_ranges(handle) if r[1] >= 4096]
    if not all_ranges:
        return []
    ranges = all_ranges
    # 换局优先扫旧地址附近 ±64MB（对象通常分配在旧地址附近）
    if near:
        lo, hi = near - 64 * 1024 * 1024, near + 64 * 1024 * 1024
        near_ranges = [(b, sz) for b, sz in all_ranges if b + sz > lo and b < hi]
        if near_ranges:
            ranges = near_ranges
    hits = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_scan_one_range, handle, b, sz, pattern, want)
                for b, sz in ranges]
        for f in futs:
            try:
                hits.extend(f.result())
            except Exception:
                pass
            if len(hits) >= want:
                break
    return hits[:want]


def pick_best(hits):
    """多个命中里挑当前对局：数据在变化/最新的那个由调用方轮询判断。"""
    if not hits:
        return None
    return hits[0]


def load_offsets():
    try:
        with open(OFFSETS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_offsets(data):
    try:
        os.makedirs(os.path.dirname(OFFSETS_PATH), exist_ok=True)
        tmp = OFFSETS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, OFFSETS_PATH)
    except Exception:
        pass


def write_live(decks, extra=None):
    """写 deck_live.json（格式与 frida_live 一致）。"""
    data = {
        "addr": hex(decks["addr"]),
        "deck": decks.get("deck"),
        "life": decks.get("life"),
        "turn": decks.get("turn"),
        "pp": decks.get("pp"),
        "maxpp": decks.get("maxpp"),
        "mulliganDone": bool(decks.get("mulliganDone")),
        "hand": decks.get("hand") or [],
        "field": decks.get("field") or [],
        "played": decks.get("played") or [],
        "destroyed": decks.get("destroyed") or [],
        "enemy": decks.get("enemy"),
        "at": int(time.time() * 1000),
    }
    if extra:
        data.update(extra)
    try:
        os.makedirs(LOGS, exist_ok=True)
        tmp = LIVE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, LIVE_PATH)
    except Exception:
        pass


def write_idle():
    try:
        os.makedirs(LOGS, exist_ok=True)
        with open(LIVE_PATH, "w", encoding="utf-8") as f:
            json.dump({"idle": True, "at": int(time.time() * 1000)}, f)
    except Exception:
        pass


def read_hand(handle, hand_ptr, instances=False):
    """读取已确认的我方手牌；空列表是合法快照，读取失败返回 None。"""
    if not hand_ptr:
        return None
    count = read_i32(handle, hand_ptr + 0x18)
    if count is None or not 0 <= count <= 10:
        return None
    size = 8 + count * 8
    slots = read_mem(handle, hand_ptr + 0x18, size)
    if slots is None or struct.unpack_from("<i", slots)[0] != count:
        return None
    cards = []
    for (item,) in struct.iter_unpack("<Q", slots[8:]):
        raw = read_mem(handle, item + 0x10, 8) if item else None
        if not raw:
            return None
        uid, cid = struct.unpack("<ii", raw)
        if not 10000000 <= cid <= 99999999:
            return None
        cards.append((uid, cid) if instances else cid)
    if slots != read_mem(handle, hand_ptr + 0x18, size):
        return None
    return cards


def read_board(handle, addr, vtable, hand_ptr, field_ptr, turn, include_hand=False):
    """只读同一对象的牌库数、生命和场面。读取期间对象变化时返回 None。"""
    head = read_mem(handle, addr, OFF_TURN + 4)
    if not head:
        return None
    klass, = struct.unpack_from("<Q", head)
    hand, field, deck, life = struct.unpack_from("<QQii", head, OFF_HAND)
    if (klass != vtable or hand != hand_ptr or field != field_ptr or not field
            or struct.unpack_from("<i", head, OFF_TURN)[0] != turn
            or not 0 <= deck <= 45 or not 0 <= life <= 30):
        return None
    count = read_i32(handle, field + 0x18)
    if count is None or not 0 <= count <= 10:
        return None
    size = 8 + count * 8
    slots = read_mem(handle, field + 0x18, size)
    if not slots or struct.unpack_from("<i", slots)[0] != count:
        return None
    units = []
    for (ptr,) in struct.iter_unpack("<Q", slots[8:]):
        raw = read_mem(handle, ptr + 0x10, 20) if ptr else None
        if not raw:
            return None
        cid, atk, hp = struct.unpack("<4xi4xii", raw)
        if not 10000000 <= cid <= 99999999:
            return None
        units.append({"cardId": cid, "atk": atk, "life": hp})
    board = {"deck": deck, "life": life, "field": units}
    if include_hand:
        cards = read_hand(handle, hand_ptr, instances=True)
        if cards is None:
            return None
        board["handUids"] = [uid for uid, _ in cards]
        board["hand"] = [cid for _, cid in cards]
    if (slots != read_mem(handle, field + 0x18, size)
            or head != read_mem(handle, addr, len(head))):
        return None
    return board


def read_object(handle, addr):
    """按偏移读一轮。返回 None 表示对象无效。"""
    deck = read_i32(handle, addr + OFF_DECK)
    life = read_i32(handle, addr + OFF_LIFE)
    if deck is None or life is None:
        return None
    if deck < 0 or deck > 45 or life < 0 or life > 30:
        return None

    def read_list(ptr_addr, kind):
        ptr = read_ptr(handle, ptr_addr)
        if not ptr:
            return []
        items = read_ptr(handle, ptr + 0x10)
        cnt = read_i32(handle, ptr + 0x18)
        if not items or cnt is None or cnt < 0 or cnt > 60:
            return []
        out = []
        for i in range(cnt):
            item = read_ptr(handle, items + i * 8)
            if not item:
                continue
            cid = read_i32(handle, item + 0x10)
            if cid is None:
                continue
            if kind == "hand":
                out.append(cid)
            elif kind == "field":
                out.append({
                    "cardId": cid,
                    "atk": read_i32(handle, item + 0x1C) or 0,
                    "life": read_i32(handle, item + 0x20) or 0,
                })
            else:
                out.append(cid)
        return out

    return {
        "addr": addr,
        "deck": deck,
        "life": life,
        "turn": read_i32(handle, addr + OFF_TURN) or 0,
        "pp": read_i32(handle, addr + OFF_PP) or 0,
        "maxpp": read_i32(handle, addr + OFF_TURN) or 0,
        "mulliganDone": (read_i32(handle, addr + OFF_MULLIGAN) or 0) != 0,
        "hand": read_list(addr + OFF_HAND, "hand"),
        "field": read_list(addr + OFF_FIELD, "field"),
        "played": read_list(addr + OFF_PLAYED, "int"),
        "destroyed": read_list(addr + OFF_DESTROYED, "int"),
    }


_VT_JS = r"""
(function () {
  var ga = Process.findModuleByName("GameAssembly.dll");
  if (!ga) { send({err: "no GameAssembly"}); return; }
  function P(n, ret, args) {
    var a = ga.findExportByName(n);
    if (a === null) throw new Error("missing " + n);
    return new NativeFunction(a, ret, args);
  }
  var domainGet = P("il2cpp_domain_get", "pointer", []);
  var threadAttach = P("il2cpp_thread_attach", "pointer", ["pointer"]);
  var getAssemblies = P("il2cpp_domain_get_assemblies", "pointer", ["pointer","pointer"]);
  var asmImage = P("il2cpp_assembly_get_image", "pointer", ["pointer"]);
  var classFromName = P("il2cpp_class_from_name", "pointer", ["pointer","pointer","pointer"]);
  var classGetMethods = P("il2cpp_class_get_methods", "pointer", ["pointer","pointer"]);
  var methodGetName = P("il2cpp_method_get_name", "pointer", ["pointer"]);
  var objClass = P("il2cpp_object_get_class", "pointer", ["pointer"]);
  var className = P("il2cpp_class_get_name", "pointer", ["pointer"]);
  var domain = domainGet();
  try { threadAttach(domain); } catch (e) {}
  var nPtr = Memory.alloc(4);
  var asms = getAssemblies(domain, nPtr);
  var n = nPtr.readInt();
  var klass = null;
  for (var i = 0; i < n && !klass; i++) {
    var asm = asms.add(i * Process.pointerSize).readPointer();
    var k = classFromName(asmImage(asm), Memory.allocUtf8String(""), Memory.allocUtf8String("BattlePlayerBase"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no class"}); return; }
  var it = Memory.alloc(Process.pointerSize);
  var m, fn = null;
  while (!(m = classGetMethods(klass, it)).isNull()) {
    var nm = ""; try { nm = methodGetName(m).readUtf8String(); } catch (e) { continue; }
    if (nm === "get_HandCardList") { fn = m.readPointer(); break; }
  }
  if (!fn) { send({err: "no getter"}); return; }
  var got = false;
  Interceptor.attach(fn, {onEnter: function (args) {
    if (got) return;
    try {
      var p = args[0];
      var cls = ""; try { cls = className(objClass(p)).readUtf8String(); } catch (e) {}
      if (cls !== "BattleStatePlayerMpo") return;
      got = true;
      send({vt: p.readPointer().toString(), mpo: p.toString()});
    } catch (e) {}
  }});
})();
"""


_HAND_JS = r"""
(function () {
  var ga = Process.findModuleByName("GameAssembly.dll");
  if (!ga) { send({err: "no GameAssembly"}); return; }
  function P(n, ret, args) {
    var a = ga.findExportByName(n);
    if (a === null) throw new Error("missing " + n);
    return new NativeFunction(a, ret, args);
  }
  var dg = P("il2cpp_domain_get", "pointer", []);
  var gas = P("il2cpp_domain_get_assemblies", "pointer", ["pointer","pointer"]);
  var ai = P("il2cpp_assembly_get_image", "pointer", ["pointer"]);
  var cfn = P("il2cpp_class_from_name", "pointer", ["pointer","pointer","pointer"]);
  var cgm = P("il2cpp_class_get_methods", "pointer", ["pointer","pointer"]);
  var mgn = P("il2cpp_method_get_name", "pointer", ["pointer"]);
  var d = dg(); var np = Memory.alloc(4);
  var asms = gas(d, np); var n = np.readInt(); var klass = null;
  for (var i = 0; i < n && !klass; i++) {
    var asm = asms.add(i * 8).readPointer();
    var k = cfn(ai(asm), Memory.allocUtf8String(""), Memory.allocUtf8String("BattlePlayerBase"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no class"}); return; }
  var it = Memory.alloc(8); var m, fn = null;
  while (!(m = cgm(klass, it)).isNull()) {
    var nm = ""; try { nm = mgn(m).readUtf8String(); } catch (e) { continue; }
    if (nm === "get_HandCardList") { fn = m.readPointer(); break; }
  }
  if (!fn) { send({err: "no getter"}); return; }
  var last = null;
  Interceptor.attach(fn, {
    onEnter: function (a) { this.self = a[0]; },
    onLeave: function (ret) {
      try {
        var lst = ret;
        if (lst.isNull()) return;
        var cards = [];
        // 尝试多个可能的 items 偏移，取第一个能读出卡号的
        var cnt = lst.add(0x18).readS32();
        var cand = [0x10, 0x20, 0x28, 0x30];
        for (var ci = 0; ci < cand.length && cards.length === 0; ci++) {
          var ip;
          try { ip = lst.add(cand[ci]).readPointer(); } catch (e) { continue; }
          if (!ip || ip.isNull()) continue;
          var tmp = [];
          for (var i = 0; i < 12; i++) {
            try {
              var e = ip.add(i * 8).readPointer();
              if (!e || e.isNull()) continue;
              var cid = e.add(0x0C).readS32();
              if (cid >= 10000000 && cid <= 99999999) tmp.push(cid);
            } catch (e2) {}
          }
          if (tmp.length) cards = tmp;
        }
        send({hand: cards, self: this.self.toString()});
      } catch (e) { send({err: String(e)}); }
    }
  });
})();
"""


_MPO_JS = r"""
(function () {
  var ga = Process.findModuleByName("GameAssembly.dll");
  if (!ga) { send({err: "no GameAssembly"}); return; }
  function P(n, ret, args) {
    var a = ga.findExportByName(n);
    if (a === null) throw new Error("missing " + n);
    return new NativeFunction(a, ret, args);
  }
  var dg = P("il2cpp_domain_get", "pointer", []);
  var gas = P("il2cpp_domain_get_assemblies", "pointer", ["pointer","pointer"]);
  var ai = P("il2cpp_assembly_get_image", "pointer", ["pointer"]);
  var cfn = P("il2cpp_class_from_name", "pointer", ["pointer","pointer","pointer"]);
  var cgm = P("il2cpp_class_get_methods", "pointer", ["pointer","pointer"]);
  var mgn = P("il2cpp_method_get_name", "pointer", ["pointer"]);
  var ogc = P("il2cpp_object_get_class", "pointer", ["pointer"]);
  var gcn = P("il2cpp_class_get_name", "pointer", ["pointer"]);

  var domain = dg();
  var np = Memory.alloc(4);
  var asms = gas(domain, np);
  var n = np.readInt();
  var klass = null;
  for (var i = 0; i < n && !klass; i++) {
    var asm = asms.add(i * 8).readPointer();
    var k = cfn(ai(asm), Memory.allocUtf8String(""), Memory.allocUtf8String("BattlePlayerBase"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no class"}); return; }

  function isMpo(p) {
    try {
      var cls = gcn(ogc(p)).readUtf8String();
      return cls === "BattleStatePlayerMpo";
    } catch (e) { return false; }
  }

  var it = Memory.alloc(8);
  var m, handFn = null, deckFn = null;
  while (!(m = cgm(klass, it)).isNull()) {
    var nm = "";
    try { nm = mgn(m).readUtf8String(); } catch (e) { continue; }
    if (nm === "get_HandCardList") handFn = m.readPointer();
    else if (nm === "get_DeckCardList") deckFn = m.readPointer();
  }

  var selfRef = null, enemyRef = null;
  function report() {
    send({
      self: selfRef ? selfRef.toString() : null,
      enemy: enemyRef ? enemyRef.toString() : null
    });
  }

  // get_HandCardList 只在我方调用 → 直接确定我方 Mpo
  if (handFn) {
    Interceptor.attach(handFn, { onEnter: function (args) {
      try {
        var p = args[0];
        if (!isMpo(p)) return;
        if (!selfRef || p.toString() !== selfRef.toString()) {
          selfRef = p;
          enemyRef = null;
          report();
        }
      } catch (e) {}
    }});
  }

  // get_DeckCardList 双方都会调 → 用来补敌方
  if (deckFn) {
    Interceptor.attach(deckFn, { onEnter: function (args) {
      try {
        var p = args[0];
        if (!isMpo(p)) return;
        if (!selfRef) { selfRef = p; report(); return; }
        if (p.toString() === selfRef.toString()) return;
        if (!enemyRef || p.toString() !== enemyRef.toString()) {
          enemyRef = p;
          report();
        }
      } catch (e) {}
    }});
  }
})();
"""

_MPO_STATE = {"self": 0, "enemy": 0, "at": 0.0}
_MPO_THREAD = None


_HAND_STATE = {"hand": None, "at": 0.0}
_HAND_THREAD = None


def _mpo_loop(pid):
    """frida 常驻 hook：只拿我方/敌方 Mpo 地址，不扫内存、不做 sample。"""
    while True:
        try:
            import frida
            session = frida.attach(pid)
            script = session.create_script(_MPO_JS)

            def on_message(msg, data):
                if msg.get("type") != "send":
                    return
                p = msg.get("payload") or {}
                if p.get("self"):
                    try:
                        _MPO_STATE["self"] = int(p["self"], 16)
                        _MPO_STATE["at"] = time.time()
                    except Exception:
                        pass
                if p.get("enemy"):
                    try:
                        _MPO_STATE["enemy"] = int(p["enemy"], 16)
                    except Exception:
                        pass

            script.on("message", on_message)
            script.load()
            while True:
                time.sleep(0.5)
                try:
                    import shadow_warchess_reader as RW
                    cur, _ = RW.find_game_pid()
                    if cur != pid:
                        break
                except Exception:
                    pass
            try:
                script.unload()
            except Exception:
                pass
            try:
                session.detach()
            except Exception:
                pass
        except Exception:
            time.sleep(1.5)


def start_mpo_hook(pid):
    """启动常驻 Mpo hook（只拿地址）。"""
    global _MPO_THREAD
    if _MPO_THREAD is not None and _MPO_THREAD.is_alive():
        return
    import threading
    t = threading.Thread(target=_mpo_loop, args=(pid,), daemon=True)
    t.start()
    _MPO_THREAD = t


def _hand_loop(pid):
    """frida 常驻 hook get_HandCardList，抽牌/打牌/刷新都会更新手牌。"""
    import threading  # noqa: F401
    while True:
        try:
            import frida
            session = frida.attach(pid)
            script = session.create_script(_HAND_JS)

            def on_message(msg, data):
                if msg.get("type") != "send":
                    return
                p = msg.get("payload") or {}
                if p.get("hand") is not None:
                    _HAND_STATE["hand"] = p["hand"]
                    _HAND_STATE["at"] = time.time()

            script.on("message", on_message)
            script.load()
            while True:
                time.sleep(0.5)
                try:
                    import shadow_warchess_reader as RW
                    cur, _ = RW.find_game_pid()
                    if cur != pid:
                        break
                except Exception:
                    pass
            try:
                script.unload()
            except Exception:
                pass
            try:
                session.detach()
            except Exception:
                pass
        except Exception:
            time.sleep(1.5)


def start_hand_hook(pid):
    global _HAND_THREAD
    if _HAND_THREAD is not None and _HAND_THREAD.is_alive():
        return
    import threading
    t = threading.Thread(target=_hand_loop, args=(pid,), daemon=True)
    t.start()
    _HAND_THREAD = t


def get_hand_via_frida(pid, timeout=8):
    """只 hook get_HandCardList 读手牌（不扫内存，不卡游戏）。"""
    try:
        import frida
        session = frida.attach(pid)
        script = session.create_script(_HAND_JS)
        result = {}

        def on_message(msg, data):
            if msg.get("type") != "send":
                return
            p = msg.get("payload") or {}
            if p.get("hand") is not None:
                result["hand"] = p["hand"]
                result["self"] = p.get("self")

        script.on("message", on_message)
        script.load()
        deadline = time.time() + timeout
        while time.time() < deadline and "hand" not in result:
            time.sleep(0.2)
        try:
            script.unload()
        except Exception:
            pass
        try:
            session.detach()
        except Exception:
            pass
        return result.get("hand")
    except Exception:
        return None


def get_vtable_via_frida(pid, timeout=30):
    """只 hook 拿 vtable（读指针，不扫内存，不卡游戏）。"""
    try:
        import frida
        session = frida.attach(pid)
        script = session.create_script(_VT_JS)
        result = {}

        def on_message(msg, data):
            if msg.get("type") != "send":
                return
            p = msg.get("payload") or {}
            if p.get("vt"):
                try:
                    result["vt"] = int(p["vt"], 16)
                    result["mpo"] = p.get("mpo")
                except Exception:
                    pass

        script.on("message", on_message)
        script.load()
        deadline = time.time() + timeout
        while time.time() < deadline and "vt" not in result:
            time.sleep(0.2)
        try:
            script.unload()
        except Exception:
            pass
        try:
            session.detach()
        except Exception:
            pass
        return result.get("vt")
    except Exception:
        return None


def scan_and_pick(pid, vtable):
    """纯外部扫描（ReadProcessMemory 分块），不注入、不卡游戏。

    frida 的 Memory.scan 会在游戏进程内扫全内存并阻塞主线程，实测会把游戏卡死；
    这里改成在外部按 4MB 分块读，读完 sleep 一下，游戏完全不受影响。
    """
    handle = open_process(pid)
    if handle is None:
        return None
    prev = 0
    try:
        prev = int(load_offsets().get("addr") or 0)
    except Exception:
        prev = 0
    try:
        hits = scan_vtable_ctypes(handle, vtable, want=40, near=prev or None)
        if not hits and prev:
            hits = scan_vtable_ctypes(handle, vtable, want=40)  # 附近没找到再全扫
    finally:
        try:
            kernel32.CloseHandle(handle)
        except Exception:
            pass
    if not hits:
        return None
    # 选 turn 最大（当前对局通常最新），多个同 turn 选 deck 更小的（打得多的）
    handle2 = open_process(pid)
    best = None
    try:
        for hit in hits:
            turn = read_i32(handle2, hit["addr"] + OFF_TURN) or 0
            played = read_ptr(handle2, hit["addr"] + OFF_PLAYED)
            played_cnt = 0
            if played:
                cnt = read_i32(handle2, played + 0x18)
                played_cnt = cnt if cnt and 0 < cnt < 100 else 0
            score = (turn, played_cnt)
            if best is None or score > best[0]:
                best = (score, hit)
    finally:
        try:
            kernel32.CloseHandle(handle2)
        except Exception:
            pass
    return best[1]["addr"] if best else hits[0]["addr"]


def main():
    # 独立启动与控制台共用同一读取器、同一把锁和同一写入路径。
    import frida_live
    return frida_live.main()


if __name__ == "__main__":
    sys.exit(main())

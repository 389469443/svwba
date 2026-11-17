#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手牌探针 —— 只读（Frida 内存读取，不写内存 / 不 hook 游戏逻辑之外的东西）。

目的：记牌器「开局能读、后续抽牌不更新、新一局全废」时，把真实内存数据抓出来，
      判定到底是：
        A) selfRef 绑定的玩家对象在开局后被替换（旧地址失效）→ 只会写 idle
        B) 手牌字段/数组语义变了（读到的是旧数组 / 偏移不对）→ 数据冻结
        C) 双方对象被搞混（读的是对手）

用途：跑一局影之诗，把 logs\\手牌探针.log 发回来即可。

用法（在 svwb-probe 目录）：
    python 手牌探针.py             # 最多跑 15 分钟，Ctrl+C 结束
    python 手牌探针.py --sec 600   # 指定秒数
"""
import io
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import frida                                                # noqa: E402
import shadow_warchess_reader as RW                         # noqa: E402

OUT = os.path.join(HERE, "logs", "手牌探针.log")

JS = r"""
(function () {
  var ga = Process.findModuleByName("GameAssembly.dll");
  if (ga === null) { send({err: "no GameAssembly.dll"}); return; }
  function P(n, ret, args) {
    var a = ga.findExportByName(n);
    if (a === null) throw new Error("missing " + n);
    return new NativeFunction(a, ret, args);
  }
  var domainGet    = P("il2cpp_domain_get", "pointer", []);
  var threadAttach = P("il2cpp_thread_attach", "pointer", ["pointer"]);
  var getAssemblies= P("il2cpp_domain_get_assemblies", "pointer", ["pointer","pointer"]);
  var asmImage     = P("il2cpp_assembly_get_image", "pointer", ["pointer"]);
  var classFromName= P("il2cpp_class_from_name", "pointer", ["pointer","pointer","pointer"]);
  var classGetMethods = P("il2cpp_class_get_methods", "pointer", ["pointer","pointer"]);
  var methodGetName   = P("il2cpp_method_get_name", "pointer", ["pointer"]);
  var objClass     = P("il2cpp_object_get_class", "pointer", ["pointer"]);
  var className    = P("il2cpp_class_get_name", "pointer", ["pointer"]);

  var domain = domainGet();
  try { threadAttach(domain); } catch (e) {}
  var nPtr = Memory.alloc(4);
  var asms = getAssemblies(domain, nPtr);
  var n = nPtr.readInt();
  var klass = null;
  for (var i = 0; i < n && !klass; i++) {
    var asm = asms.add(i * Process.pointerSize).readPointer();
    var img = asmImage(asm);
    var k = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("BattlePlayerBase"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no class BattlePlayerBase"}); return; }

  var iter = Memory.alloc(Process.pointerSize);
  var handFn = null, deckFn = null, names = [];
  var m;
  while (!(m = classGetMethods(klass, iter)).isNull()) {
    var nm = methodGetName(m).readUtf8String();
    if (names.indexOf(nm) < 0) names.push(nm);
    if (nm === "get_HandCardList") handFn = m.readPointer();
    if (nm === "get_DeckCardList") deckFn = m.readPointer();
  }
  if (handFn === null) { send({err: "no get_HandCardList", methods: names}); return; }

  function hex(p, len) {
    try {
      var a = new Uint8Array(p.readByteArray(len));
      var s = "";
      for (var i = 0; i < a.length; i++) s += ("0" + a[i].toString(16)).slice(-2);
      return s;
    } catch (e) { return "ERR"; }
  }
  function cls(p) {
    try { return className(objClass(p)).readUtf8String(); } catch (e) { return "?"; }
  }

  // 候选玩家对象：addr -> 最后一次被 getter 调用的时间与次数
  var cands = {};
  function note(p, which) {
    var k = p.toString();
    var c = cands[k];
    if (!c) { c = {addr: k, first: Date.now(), last: 0, hand: 0, deck: 0}; cands[k] = c; }
    c.last = Date.now();
    c[which] += 1;
  }
  if (deckFn !== null) {
    Interceptor.attach(deckFn, {onEnter: function (a) {
      try { note(a[0], "deck"); } catch (e) {}
    }});
  }
  Interceptor.attach(handFn, {onEnter: function (a) {
    try { note(a[0], "hand"); } catch (e) {}
  }});

  // 读一个数组/列表：同时报告两种解释，便于判断到底是 T[] 还是 List<T>
  function arr(arrPtr) {
    var out = {ptr: arrPtr.toString(), raw: null, size_q18: null, items: null,
               as_array: [], as_list: []};
    if (arrPtr.isNull()) return out;
    out.raw = hex(arrPtr, 0x60);
    try { out.size_q18 = arrPtr.add(0x18).readS32(); } catch (e) {}
    try { out.items = arrPtr.add(0x10).readPointer().toString(); } catch (e) {}
    var n1 = out.size_q18;
    if (n1 !== null && n1 >= 0 && n1 <= 60) {
      for (var i = 0; i < Math.min(n1, 12); i++) {
        try {
          var e1 = arrPtr.add(0x20 + i * 8).readPointer();
          out.as_array.push(e1.isNull() ? 0 : e1.add(0x14).readS32());
        } catch (e) { out.as_array.push(null); }
      }
    }
    try {
      var itemsP = arrPtr.add(0x10).readPointer();
      if (!itemsP.isNull()) {
        for (var j = 0; j < Math.min(n1 || 0, 12); j++) {
          var e2 = itemsP.add(0x20 + j * 8).readPointer();
          out.as_list.push(e2.isNull() ? 0 : e2.add(0x14).readS32());
        }
      }
    } catch (e) {}
    return out;
  }

  var seen = {};
  function dump(p) {
    var o = {addr: p.toString(), cls: cls(p)};
    if (!seen[o.addr]) {
      seen[o.addr] = 1;
      o.rawhead = hex(p, 0xA0);          // 对象头部原始字节，用来核对字段偏移
    }
    try { o.deck = p.add(32).readS32(); } catch (e) { o.deck = null; }
    try { o.life = p.add(36).readS32(); } catch (e) { o.life = null; }
    try { o.turn = p.add(0x38).readS32(); } catch (e) { o.turn = null; }
    try { o.pp = p.add(100).readS32(); } catch (e) { o.pp = null; }
    try { o.mull = p.add(0x5C).readS8() !== 0; } catch (e) { o.mull = null; }
    try { o.hand = arr(p.add(16).readPointer()); } catch (e) { o.hand = {err: String(e)}; }
    try { o.field = arr(p.add(24).readPointer()); } catch (e) {}
    return o;
  }

  var lastSig = null, lastBeat = 0;
  var timer = setInterval(function () {
    var now = Date.now();
    var live = [];
    for (var k in cands) {
      var c = cands[k];
      if (now - c.last < 120000) live.push(c);
    }
    var objs = [];
    for (var i = 0; i < live.length; i++) {
      try { objs.push(dump(ptr(live[i].addr))); } catch (e) {}
    }
    var sig = JSON.stringify(objs.map(function (o) {
      return [o.addr, o.cls, o.deck, o.life, o.turn, o.mull,
              (o.hand && o.hand.size_q18), (o.hand && o.hand.as_array),
              (o.hand && o.hand.as_list)];
    }));
    if (sig !== lastSig || now - lastBeat > 5000) {
      lastSig = sig;
      lastBeat = now;
      send({t: now, cands: live, objs: objs});
    }
  }, 500);

  send({hooked: true, methods: names});
  rpc.exports = {stop: function () {
    try { clearInterval(timer); } catch (e) {}
    try { Interceptor.detachAll(); } catch (e) {}
    return true;
  }};
})();
"""


def _load_deck_ids():
    """读出用户导入的卡组（多套取并集），用来判断哪个对象才是「我」。"""
    import json
    p = os.path.join(HERE, "gamedata", "user_decks.json")
    try:
        with io.open(p, encoding="utf-8") as fh:
            d = json.load(fh) or {}
    except Exception:
        return set()
    ids = set()
    for item in d.get("decks") or []:
        for c in item.get("cards") or []:
            if isinstance(c, int):
                ids.add(c)
    return ids


def main():
    secs = 900
    if "--sec" in sys.argv:
        try:
            secs = int(sys.argv[sys.argv.index("--sec") + 1])
        except Exception:
            pass

    pid, path = RW.find_game_pid()
    if not pid:
        print("× 没找到游戏进程。请先开游戏再运行本探针。")
        return 1
    print("目标进程 %d  %s" % (pid, path))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fh = io.open(OUT, "a", encoding="utf-8")
    deck_ids = _load_deck_ids()
    print("已导入卡组：%d 个不同卡 id（用于判断哪个对象是「我方」）" % len(deck_ids))

    def log(line):
        print(line, flush=True)
        fh.write(line + "\n")
        fh.flush()

    log("\n===== %s  探针启动 pid=%d =====" % (time.strftime("%H:%M:%S"), pid))

    session = frida.attach(pid)
    script = session.create_script(JS)

    def on_message(msg, data):
        if msg.get("type") != "send":
            log("MSG %r" % (msg,))
            return
        p = msg.get("payload") or {}
        if p.get("err"):
            log("× JS 报错：%s  methods=%s" % (p.get("err"), p.get("methods")))
            return
        if p.get("hooked"):
            log("hook 就绪，类方法数 %d" % len(p.get("methods") or []))
            return
        ts = time.strftime("%H:%M:%S", time.localtime(p.get("t", 0) / 1000.0))
        cands = p.get("cands") or []
        if not cands:
            log("[%s] 还没有任何玩家对象被 getter 调用过" % ts)
            return
        for o in p.get("objs") or []:
            h = o.get("hand") or {}
            if o.get("rawhead"):
                log("[%s] ★ 新对象 %s %s\n        0x10~0xB0 = %s"
                    % (ts, o.get("addr"), o.get("cls"), o.get("rawhead")))
            log("[%s] %s %s deck=%s life=%s turn=%s mull=%s | handPtr=%s size=%s"
                " array=%s list=%s | 牌组重合 array=%d list=%d | field=%s"
                % (ts, o.get("addr"), o.get("cls"), o.get("deck"), o.get("life"),
                   o.get("turn"), o.get("mull"), h.get("ptr"), h.get("size_q18"),
                   h.get("as_array"), h.get("as_list"),
                   len(deck_ids & set(h.get("as_array") or [])),
                   len(deck_ids & set(h.get("as_list") or [])),
                   len((o.get("field") or {}).get("as_array") or [])))
        log("[%s]   候选：%s" % (ts, ", ".join(
            "%s(hand%d/deck%d)" % (c["addr"][-8:], c["hand"], c["deck"])
            for c in cands)))

    script.on("message", on_message)
    script.load()
    t0 = time.time()
    try:
        while time.time() - t0 < secs:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    try:
        script.exports_sync.stop()
    except Exception:
        pass
    try:
        session.detach()
    except Exception:
        pass
    log("===== 探针结束，日志：%s =====" % OUT)
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

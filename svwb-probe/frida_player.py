#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frida: 只对 BattleStatePlayerMpo 读 5 个 List + 卡 id（2026-10-21）。"""
import sys
import time

import frida

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 10852
WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 60

JS = r"""
(function () {
  var ga = Process.findModuleByName("GameAssembly.dll");
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
    var img = asmImage(asm);
    var k = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("BattlePlayerBase"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no class"}); return; }

  var iter = Memory.alloc(Process.pointerSize);
  var fn = null;
  var m;
  while (!(m = classGetMethods(klass, iter)).isNull()) {
    var nm = methodGetName(m).readUtf8String();
    if (nm === "get_HandCardList") { fn = m.readPointer(); break; }
  }
  if (fn === null) { send({err: "no getter"}); return; }

  function readIds(list) {
    if (list.isNull()) return null;
    var size = list.add(0x18).readS32();
    var items = list.add(0x10).readPointer();
    var out = {size: size, items: items.toString(), ids: [], idsAt0: []};
    if (items.isNull() || size < 0 || size > 60) return out;
    for (var i = 0; i < Math.min(size, 8); i++) {
      try {
        var e = items.add(0x20 + i * 8).readPointer();
        var p = e.add(0xA8).readPointer();
        out.ids.push(p.isNull() ? 0 : p.add(0x90).readS32());
      } catch (ex) {}
      try {
        var e2 = items.add(i * 8).readPointer();
        var p2 = e2.add(0xA8).readPointer();
        out.idsAt0.push(p2.isNull() ? 0 : p2.add(0x90).readS32());
      } catch (ex) {}
    }
    return out;
  }

  var printed = 0;
  Interceptor.attach(fn, {
    onEnter: function (args) {
      if (printed >= 2) return;
      var self = args[0];
      var cls = "";
      try { cls = className(objClass(self)).readUtf8String(); } catch (e) {}
      if (cls !== "BattleStatePlayerMpo") return;
      printed++;
      var info = {self: self.toString(), cls: cls};
      [["hand", 160], ["deck", 168], ["start", 176], ["cemetery", 200]].forEach(function (kv) {
        try { info[kv[0]] = readIds(self.add(kv[1]).readPointer()); }
        catch (e) { info[kv[0]] = "err"; }
      });
      send(info);
    }
  });
  send({hooked: true});
})();
"""


def main():
    print("attach", PID, "wait", WAIT)
    session = frida.attach(PID)
    script = session.create_script(JS)
    script.on("message", lambda m, d: print("MSG:", m))
    script.load()
    time.sleep(WAIT)
    try:
        script.unload()
    except Exception:
        pass
    session.detach()


if __name__ == "__main__":
    main()

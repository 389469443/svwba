#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frida: hook GetCardIdList 的 onLeave，读返回值 List<int>（卡组 40 张）。"""
import sys
import time

import frida

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 0
WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 120

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

  var domain = domainGet();
  try { threadAttach(domain); } catch (e) {}
  var nPtr = Memory.alloc(4);
  var asms = getAssemblies(domain, nPtr);
  var n = nPtr.readInt();
  var klass = null;
  for (var i = 0; i < n && !klass; i++) {
    var asm = asms.add(i * Process.pointerSize).readPointer();
    var img = asmImage(asm);
    var k = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("DeckData"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no DeckData"}); return; }
  var iter = Memory.alloc(Process.pointerSize);
  var fns = [];
  var m;
  while (!(m = classGetMethods(klass, iter)).isNull()) {
    var nm = methodGetName(m).readUtf8String();
    if (nm === "GetCardIdList") fns.push(m.readPointer());
  }
  send({hooked: fns.length});

  function tryList(p) {
    if (p.isNull()) return null;
    try {
      var sz = p.add(0x18).readS32();
      if (sz < 30 || sz > 60) return null;
      var items = p.add(0x10).readPointer();
      if (items.isNull()) return null;
      var ids = [];
      for (var i = 0; i < Math.min(sz, 50); i++) {
        ids.push(items.add(0x20 + i * 4).readS32());
      }
      return {size: sz, ids: ids};
    } catch (e) { return null; }
  }

  var sent = 0;
  fns.forEach(function (fn) {
    if (fn.isNull()) return;
    try {
      Interceptor.attach(fn, {
        onLeave: function (retval) {
          if (sent >= 4) return;
          var r = tryList(retval);
          if (r) { sent++; send(r); }
        }
      });
    } catch (e) {}
  });
})();
"""


def main():
    if not PID:
        print("usage: frida_deck_ret.py <pid> [seconds]")
        return
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

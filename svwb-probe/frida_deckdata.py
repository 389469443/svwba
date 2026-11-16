#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frida: hook DeckData.get_CardIdList，读 40 张卡组。"""
import sys
import time

import frida

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 6524
WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 90

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
    var k = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("DeckData"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no DeckData"}); return; }
  var iter = Memory.alloc(Process.pointerSize);
  var fns = [];
  var m;
  while (!(m = classGetMethods(klass, iter)).isNull()) {
    var nm = methodGetName(m).readUtf8String();
    if (nm.indexOf("CardIdList") >= 0 || nm.indexOf("CardId") >= 0) {
      fns.push({name: nm, fn: m.readPointer()});
    }
  }
  send({methods: fns.map(function (x) { return x.name; })});
  var sent = 0;
  fns.forEach(function (it) {
    if (it.fn.isNull()) return;
    try {
      Interceptor.attach(it.fn, {
        onEnter: function (args) {
          if (sent >= 3) return;
          var self = args[0];
          try {
            var lst = self.add(0x40).readPointer();
            if (lst.isNull()) return;
            var sz = lst.add(0x18).readS32();
            var items = lst.add(0x10).readPointer();
            var ids = [];
            if (!items.isNull() && sz > 0 && sz <= 60) {
              for (var i = 0; i < Math.min(sz, 45); i++) {
                ids.push(items.add(0x20 + i * 4).readS32());
              }
            }
            sent++;
            send({m: it.name, size: sz, ids: ids});
          } catch (ex) { send({err: String(ex)}); }
        }
      });
    } catch (e) {}
  });
  send({hooked: fns.length});
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

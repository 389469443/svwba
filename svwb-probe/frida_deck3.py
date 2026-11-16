#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frida: hook DeckData 的全部方法，看准备界面到底调用哪些。"""
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
  var hooked = [];
  var m;
  while (!(m = classGetMethods(klass, iter)).isNull()) {
    var nm = methodGetName(m).readUtf8String();
    var fn = m.readPointer();
    if (fn.isNull()) continue;
    (function (name) {
      try {
        Interceptor.attach(fn, {
          onEnter: function (args) {
            try {
              var cls = "";
              try { cls = className(objClass(args[0])).readUtf8String(); } catch (e) {}
              send({m: name, cls: cls, self: args[0].toString()});
            } catch (e) {}
          }
        });
        hooked.push(name);
      } catch (e) {}
    })(nm);
  }
  send({hooked: hooked.length, names: hooked.slice(0, 40)});
})();
"""


def main():
    if not PID:
        print("usage: frida_deck3.py <pid> [seconds]")
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

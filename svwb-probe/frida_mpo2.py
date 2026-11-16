#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frida: hook get_HandCardList，对 BattleStatePlayerMpo 直接 dump 自身类字段。"""
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
  var classGetFields = P("il2cpp_class_get_fields", "pointer", ["pointer","pointer"]);
  var fieldGetName = P("il2cpp_field_get_name", "pointer", ["pointer"]);
  var fieldGetOffset = P("il2cpp_field_get_offset", "int", ["pointer"]);

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

  var dumped = 0;
  Interceptor.attach(fn, {
    onEnter: function (args) {
      if (dumped >= 1) return;
      var self = args[0];
      var k = null, cls = "";
      try { k = objClass(self); cls = className(k).readUtf8String(); } catch (e) {}
      if (cls !== "BattleStatePlayerMpo") return;
      dumped++;
      send({self: self.toString(), cls: cls});
      var it = Memory.alloc(Process.pointerSize);
      var f;
      while (!(f = classGetFields(k, it)).isNull()) {
        var nm = fieldGetName(f).readUtf8String();
        if (/Hand|Deck|Card|Cemetery|Banish|Life|Hero/i.test(nm)) {
          send({name: nm, off: fieldGetOffset(f)});
        }
      }
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

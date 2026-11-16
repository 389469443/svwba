#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frida: dump BattleStatePlayerMpo 的真实字段名+偏移（对照 dump.cs）。"""
import sys
import time

import frida

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 10852
WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 15

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
  var classGetFields = P("il2cpp_class_get_fields", "pointer", ["pointer","pointer"]);
  var fieldGetName = P("il2cpp_field_get_name", "pointer", ["pointer"]);
  var fieldGetOffset = P("il2cpp_field_get_offset", "int", ["pointer"]);
  var classParent = P("il2cpp_class_get_parent", "pointer", ["pointer"]);
  var className = P("il2cpp_class_get_name", "pointer", ["pointer"]);

  var domain = domainGet();
  try { threadAttach(domain); } catch (e) { send({attachErr: String(e)}); }
  var nPtr = Memory.alloc(4);
  var asms = getAssemblies(domain, nPtr);
  var n = nPtr.readInt();
  var klass = null;
  for (var i = 0; i < n && !klass; i++) {
    var asm = asms.add(i * Process.pointerSize).readPointer();
    var img = asmImage(asm);
    var k = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("BattleStatePlayerMpo"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no class"}); return; }

  var depth = 0;
  while (!klass.isNull() && depth < 6) {
    var cname = className(klass).readUtf8String();
    send({cls: cname});
    var iter = Memory.alloc(Process.pointerSize);
    var f;
    while (!(f = classGetFields(klass, iter)).isNull()) {
      var nm = fieldGetName(f).readUtf8String();
      var off = fieldGetOffset(f);
      if (nm.indexOf("Hand") >= 0 || nm.indexOf("Deck") >= 0 ||
          nm.indexOf("Cemetery") >= 0 || nm.indexOf("Banish") >= 0 ||
          nm.indexOf("CardList") >= 0) {
        send({name: nm, off: off});
      }
    }
    klass = classParent(klass);
    depth++;
  }
})();
"""


def main():
    print("attach", PID)
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Frida: GC 堆遍历找 BattlePlayerBase（直接读对象头 + 父类链 0x58，不调用易崩 API）。
用法： python -u frida_gc2.py <pid>
"""
import sys
import time

import frida

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 5352
CLASS_PARENT_OFF = 0x58   # Il2CppClass.parent（il2cpp.h）

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
  var foreachHeap = P("il2cpp_gc_foreach_heap", "void", ["pointer","pointer"]);

  var domain = domainGet();
  try { threadAttach(domain); } catch (e) { send({attachErr: String(e)}); }
  var nPtr = Memory.alloc(4);
  var asms = getAssemblies(domain, nPtr);
  var n = nPtr.readInt();
  var target = null;
  for (var i = 0; i < n && !target; i++) {
    var asm = asms.add(i * Process.pointerSize).readPointer();
    var img = asmImage(asm);
    var k = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("BattlePlayerBase"));
    if (!k.isNull()) target = k;
  }
  if (!target) { send({err: "no class"}); return; }
  send({klass: target.toString(), name: target.add(0x10).readPointer().readUtf8String()});

  var found = [];
  var seen = 0;
  var cb = new NativeCallback(function (obj, user) {
    if (obj.isNull()) return;
    seen++;
    try {
      var k = obj.readPointer();  // 对象头 = Il2CppClass*
      var depth = 0;
      while (!k.isNull() && depth < 20) {
        if (k.equals(target)) { found.push(obj); return; }
        k = k.add(0x58).readPointer();
        depth++;
      }
    } catch (e) {}
  }, "void", ["pointer", "pointer"]);
  foreachHeap(cb, ptr(0));
  send({seen: seen, found: found.length});
  for (var j = 0; j < Math.min(found.length, 6); j++) {
    send({obj: found[j].toString()});
  }
})();
"""


def main():
    print("attach", PID)
    session = frida.attach(PID)
    script = session.create_script(JS)
    script.on("message", lambda m, d: print("MSG:", m))
    script.load()
    time.sleep(8)
    try:
        script.unload()
    except Exception:
        pass
    session.detach()


if __name__ == "__main__":
    main()

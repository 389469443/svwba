#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探针：hook get_DeckCardList 的 onLeave，在返回值对象里扫字段找 T[] 数组，
再读数组元素（BattleCardBase）的卡 id。"""
import sys
import time

import frida

PID = int(sys.argv[1])
WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 30

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
    var k = classFromName(img, Memory.allocUtf8String(""),
                          Memory.allocUtf8String("BattlePlayerBase"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no class"}); return; }
  var fn = null;
  var miter = Memory.alloc(Process.pointerSize);
  var mm;
  while (!(mm = classGetMethods(klass, miter)).isNull()) {
    if (methodGetName(mm).readUtf8String() === "get_DeckCardList") {
      fn = mm.readPointer();
      var fn8 = mm.add(8).readPointer();
      break;
    }
  }
  if (fn === null) { send({err: "no getter"}); return; }

  var hits = 0;
  function hookAt(addr, tag) {
    try {
      Interceptor.attach(addr, {
        onEnter: function () { this.hit = true; },
        onLeave: function (retval) {
          hits++;
          if (hits > 4) return;
          var p = retval;
          if (p.isNull()) { send({tag: tag, ret: "null"}); return; }
          var info = {tag: tag, ret_cls: "?"};
          try { info.ret_cls = className(objClass(p)).readUtf8String(); } catch (e) {}
          var fields = [];
          for (var off = 8; off <= 0x50; off += 8) {
            try {
              var v = p.add(off).readPointer();
              if (v.isNull() || v.compare(0x10000) < 0) continue;
              var vc = "?";
              try { vc = className(objClass(v)).readUtf8String(); } catch (e) { vc = "?"; }
              var len = -1;
              try { len = v.add(0x18).readS32(); } catch (e) {}
              fields.push("0x" + off.toString(16) + "=" + vc + "(len=" + len + ")");
            } catch (e) {}
          }
          info.fields = fields;
          send(info);
        }
      });
    } catch (e) {}
  }
  hookAt(fn, "m+0");
  if (fn8 && fn8.compare(0x10000) > 0) hookAt(fn8, "m+8");
  send({hooked: true});
})();
"""


def main():
    session = frida.attach(PID)
    script = session.create_script(JS)

    def on_message(m, d):
        print(m, flush=True)

    script.on("message", on_message)
    script.load()
    t0 = time.time()
    while time.time() - t0 < WAIT:
        time.sleep(1)
    try:
        script.unload()
    except Exception:
        pass
    session.detach()


if __name__ == "__main__":
    main()

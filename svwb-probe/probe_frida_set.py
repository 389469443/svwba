#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探针：hook set_DeckCardList / set_BattleStartDeckCardList，命中时打印 self
类名 + 新 List 参数的内容（元素类名 / 卡 id）。"""
import sys
import time

import frida

PID = int(sys.argv[1])
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
    var k = classFromName(img, Memory.allocUtf8String(""),
                          Memory.allocUtf8String("BattlePlayerBase"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no class"}); return; }

  var targets = {};
  var miter = Memory.alloc(Process.pointerSize);
  var mm;
  while (!(mm = classGetMethods(klass, miter)).isNull()) {
    var mn = methodGetName(mm).readUtf8String();
    if (mn === "set_DeckCardList" || mn === "set_BattleStartDeckCardList" ||
        mn === "set_HandCardList") {
      targets[mn] = mm.readPointer();
    }
  }

  function probePtr(base, off, depth) {
    try {
      var p = base.add(off).readPointer();
      if (p.isNull() || p.compare(0x10000) < 0) return null;
      var cn = className(objClass(p)).readUtf8String();
      var len = "";
      try { len = p.add(0x18).readS32(); } catch (e) {}
      return "0x" + off.toString(16) + "=" + cn + "(len=" + len + ")";
    } catch (e) { return null; }
  }

  var seen = {};
  Object.keys(targets).forEach(function (name) {
    Interceptor.attach(targets[name], {
      onEnter: function (args) {
        var self = args[0];
        var cname = "";
        try { cname = className(objClass(self)).readUtf8String(); } catch (e) { return; }
        var key = name + "/" + cname;
        if (seen[key]) return;
        seen[key] = true;
        var arr = args[1];
        var desc = "";
        if (!arr.isNull()) {
          try { desc = "arg1=" + className(objClass(arr)).readUtf8String()
                       + " len=" + arr.add(0x18).readS32(); } catch (e) { desc = "arg1?"; }
        }
        var ptrs = [];
        for (var off = 8; off <= 0x40; off += 8) {
          var s = probePtr(self, off, 1);
          if (s) ptrs.push(s);
        }
        send({method: name, self: cname, arg: desc, self_ptrs: ptrs});
      }
    });
  });
  send({hooked: Object.keys(targets)});
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

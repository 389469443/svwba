#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探针：hook BattlePlayerBase 的 get_DeckCardList / get_BattleStartDeckCardList，
打印真实卡组 List 的 size 与每张卡的 CardId。

只读，不写游戏状态；拿到数据后脚本自己 unload。
"""
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

  function dumpList(self, off) {
    try {
      var p = self.add(off).readPointer();
      if (p.isNull()) return "null";
      var cnt = p.add(0x18).readS32();
      if (cnt < 0 || cnt > 200) return "bad size=" + cnt;
      var ids = [];
      for (var i = 0; i < Math.min(cnt, 6); i++) {
        var e = p.add(0x20 + i * 8).readPointer();
        if (e.isNull()) { ids.push("null"); continue; }
        var ecls = className(objClass(e)).readUtf8String();
        var cid = e.add(0xA8).readPointer().add(0x90).readS32();
        ids.push(ecls + ":" + cid);
      }
      return "size=" + cnt + " [" + ids.join(" | ") + "]";
    } catch (e) {
      return "err " + e;
    }
  }

  var targets = {};
  var miter = Memory.alloc(Process.pointerSize);
  var mm;
  while (!(mm = classGetMethods(klass, miter)).isNull()) {
    var mn = methodGetName(mm).readUtf8String();
    if (mn === "get_DeckCardList" || mn === "get_BattleStartDeckCardList" ||
        mn === "get_HandCardList") {
      targets[mn] = mm.readPointer();
    }
  }
  var hits = 0;
  Object.keys(targets).forEach(function (name) {
    Interceptor.attach(targets[name], {
      onEnter: function (args) {
        if (hits >= 6) return;
        var self = args[0];
        var cname = "";
        try { cname = className(objClass(self)).readUtf8String(); } catch (e) { return; }
        hits++;
        send({method: name, self: cname,
              hand: dumpList(self, 0xA0),
              deck: dumpList(self, 0xA8),
              start: dumpList(self, 0xB0),
              hand2: dumpList(self, 0x10),
              deck2: dumpList(self, 0x20)});
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

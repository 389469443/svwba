#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探针：hook BattlePlayerBase 的战斗事件，命中时打印 self 类名 + 卡组列表。

事件方法（抽牌/加牌/回合开始/出牌）由战斗逻辑调用，self 是真正的 BattlePlayerBase。
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

  var events = ["OnDrawCards", "OnAddDeckEvent", "OnTurnStartComplete",
                "OnPlayCard", "OnAddHandCardEvent", "OnMulliganEnd",
                "OnSetupCardEvent", "OnUpdateDeck"];
  var targets = {};
  var miter = Memory.alloc(Process.pointerSize);
  var mm;
  while (!(mm = classGetMethods(klass, miter)).isNull()) {
    var mn = methodGetName(mm).readUtf8String();
    if (events.indexOf(mn) >= 0) targets[mn] = mm.readPointer();
  }

  function dumpList(base, off) {
    try {
      var p = base.add(off).readPointer();
      if (p.isNull()) return "null";
      var cnt = p.add(0x18).readS32();
      if (cnt < 0 || cnt > 200) return "bad size=" + cnt;
      var ids = [];
      for (var i = 0; i < Math.min(cnt, 8); i++) {
        var e = p.add(0x20 + i * 8).readPointer();
        if (e.isNull()) { ids.push("null"); continue; }
        ids.push(e.add(0xA8).readPointer().add(0x90).readS32());
      }
      return "size=" + cnt + " [" + ids.join(",") + "]";
    } catch (e) { return "err " + e; }
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
        send({method: name, self: cname,
              start: dumpList(self, 0xB0),
              deck: dumpList(self, 0xA8),
              hand: dumpList(self, 0xA0)});
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

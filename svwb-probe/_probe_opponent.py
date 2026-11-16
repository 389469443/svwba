#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测对手场上随从：hook BattlePlayerBase 的 get_FieldCardList / get_FieldCards。

游戏在对局中时运行：python _probe_opponent.py
游戏渲染双方战场时会分别调用（self = 我方/对手 Mpo），从而拿到对手的 field。
"""
import json
import sys
import time

import frida

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
    var k = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("BattlePlayerBase"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no class"}); return; }

  function readField(arr) {
    if (arr.isNull()) return [];
    var cnt = arr.add(0x18).readS32();
    if (cnt <= 0 || cnt > 30) return [];
    var out = [];
    for (var i = 0; i < Math.min(cnt, 20); i++) {
      try {
        var e = arr.add(0x20 + i * 8).readPointer();
        if (e.isNull()) continue;
        out.push({cardId: e.add(0x14).readS32(),
                  atk: e.add(0x1C).readS32(),
                  life: e.add(0x20).readS32()});
      } catch (e2) {}
    }
    return out;
  }

  var seen = {};
  var it = Memory.alloc(Process.pointerSize);
  var m;
  var hooked = 0;
  while (!(m = classGetMethods(klass, it)).isNull()) {
    var nm = "";
    try { nm = methodGetName(m).readUtf8String(); } catch (e) { continue; }
    if (nm !== "get_FieldCardList" && nm !== "get_FieldCards") continue;
    try {
      var fn = m.readPointer();
      Interceptor.attach(fn, {
        onEnter: function (args) {
          var self = args[0];
          var addr = self.toString();
          if (seen[addr]) return;
          seen[addr] = true;
          var cls = "";
          try { cls = className(objClass(self)).readUtf8String(); } catch (e) {}
          var f = readField(self.add(24).readPointer());
          send({fieldProbe: {cls: cls, addr: addr,
                             life: self.add(36).readS32(),
                             deck: self.add(32).readS32(),
                             field: f}});
        }
      });
      hooked++;
    } catch (e) {}
  }
  send({hooked: hooked});
})();
"""


def main():
    try:
        import shadow_warchess_reader as RW
        pid, _ = RW.find_game_pid()
    except Exception as e:  # noqa: BLE001
        print("找不到游戏进程：%s" % e)
        return 1
    if not pid:
        print("游戏没在跑（或不在对局），先开一局再跑本脚本")
        return 1
    print("attach", pid, "探测 10 秒……（游戏会短暂卡顿一下）")
    session = frida.attach(pid)
    script = session.create_script(JS)
    got = []

    def on_message(m, d):
        if m.get("type") != "send":
            return
        p = m.get("payload") or {}
        if "hooked" in p:
            print("hook 到", p["hooked"], "个方法")
        if isinstance(p.get("fieldProbe"), dict):
            got.append(p["fieldProbe"])
            print(json.dumps(p["fieldProbe"], ensure_ascii=False))

    script.on("message", on_message)
    script.load()
    time.sleep(10)
    try:
        script.unload()
    except Exception:
        pass
    try:
        session.detach()
    except Exception:
        pass
    print("共", len(got), "个玩家 Mpo 的 field")
    return 0


if __name__ == "__main__":
    sys.exit(main())

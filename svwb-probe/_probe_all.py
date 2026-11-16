#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""综合探测：对手场面 + 胜负。

hook BattlePlayerBase.get_FieldCardList/get_FieldCards（双方 Mpo 的 field）
和 BattleManagerBase.get_IsBattleEnd / JudgeBattleResult（对局结束/结果）。
游戏在对局中时运行：python _probe_all.py
"""
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

  function findClass(name) {
    for (var i = 0; i < n; i++) {
      var asm = asms.add(i * Process.pointerSize).readPointer();
      var img = asmImage(asm);
      var k = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String(name));
      if (!k.isNull()) return k;
    }
    return null;
  }

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

  var seenField = {};
  var pb = findClass("BattlePlayerBase");
  var bm = findClass("BattleManagerBase");
  var hooked = [];
  if (pb) {
    var it = Memory.alloc(Process.pointerSize);
    var m;
    while (!(m = classGetMethods(pb, it)).isNull()) {
      var nm = "";
      try { nm = methodGetName(m).readUtf8String(); } catch (e) { continue; }
      if (nm !== "get_DeckCardList" && nm !== "get_HandCardList" && nm !== "get_BattleStartDeckCardList") continue;
      try {
        var fn = m.readPointer();
        (function (name) {
          Interceptor.attach(fn, {
            onEnter: function (args) {
              var self = args[0];
              var addr = self.toString();
              if (seenField[addr]) return;
              seenField[addr] = true;
              var cls = "";
              try { cls = className(objClass(self)).readUtf8String(); } catch (e) {}
              send({field: {cls: cls, addr: addr,
                            isMain: self.add(17).readS8(),
                            raw: (function () { var a = []; for (var o = 16; o <= 28; o++) { try { a.push(self.add(o).readU8()); } catch (e) {} } return a; })(),
                            life: self.add(36).readS32(),
                            field: readField(self.add(24).readPointer())}});
            }
          });
        })(nm);
        hooked.push("PB." + nm);
      } catch (e) {}
    }
  }
  if (bm) {
    var it2 = Memory.alloc(Process.pointerSize);
    var m2;
    while (!(m2 = classGetMethods(bm, it2)).isNull()) {
      var nm2 = "";
      try { nm2 = methodGetName(m2).readUtf8String(); } catch (e) { continue; }
      if (nm2 !== "get_IsBattleEnd" && nm2 !== "JudgeBattleResult") continue;
      try {
        var fn2 = m2.readPointer();
        (function (name) {
          Interceptor.attach(fn2, {
            onEnter: function (args) { this.name = name; },
            onLeave: function (retval) {
              try {
                send({result: {name: this.name, retInt: retval.toInt32()}});
              } catch (e) {}
            }
          });
        })(nm2);
        hooked.push("BM." + nm2);
      } catch (e) {}
    }
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
        print("游戏没在跑")
        return 1
    print("attach", pid, "探测 15 秒……（会卡一下）")
    session = frida.attach(pid)
    script = session.create_script(JS)
    seen = set()

    def on_message(m, d):
        if m.get("type") != "send":
            return
        p = m.get("payload") or {}
        if "hooked" in p:
            print("hook:", p["hooked"])
        if isinstance(p.get("field"), dict):
            v = p["field"]
            key = ("F", v.get("addr"))
            if key in seen:
                return
            seen.add(key)
            print("FIELD", v.get("addr"), "raw=", v.get("raw"), "life=", v.get("life"), "field=", v.get("field"))
        if isinstance(p.get("result"), dict):
            r = p["result"]
            key = ("R", r.get("name"), r.get("retInt"))
            if key in seen:
                return
            seen.add(key)
            print("RESULT", r)

    script.on("message", on_message)
    script.load()
    time.sleep(15)
    try:
        script.unload()
    except Exception:
        pass
    try:
        session.detach()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

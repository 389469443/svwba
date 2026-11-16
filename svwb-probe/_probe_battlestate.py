#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测 BattleState 类（找对手 Mpo 引用）。

游戏在对局中时运行：python _probe_battlestate.py
遍历所有 assembly 的类，找类名含 "BattleState" 的（不含 Mpo），打印字段
（名字/偏移/类型）和静态字段值，用来定位「双方玩家数组」。
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
  var className = P("il2cpp_class_get_name", "pointer", ["pointer"]);
  var classGetFields = P("il2cpp_class_get_fields", "pointer", ["pointer","pointer"]);
  var fieldGetName = P("il2cpp_field_get_name", "pointer", ["pointer"]);
  var fieldGetOffset = P("il2cpp_field_get_offset", "int", ["pointer"]);
  var fieldGetType = P("il2cpp_field_get_type", "pointer", ["pointer"]);
  var typeGetName = P("il2cpp_type_get_name", "pointer", ["pointer"]);
  var imageGetClassCount = P("il2cpp_image_get_class_count", "int", ["pointer"]);
  var imageGetClass = P("il2cpp_image_get_class", "pointer", ["pointer","uint"]);

  var domain = domainGet();
  try { threadAttach(domain); } catch (e) {}
  var nPtr = Memory.alloc(4);
  var asms = getAssemblies(domain, nPtr);
  var n = nPtr.readInt();
  var out = [];
  for (var i = 0; i < n; i++) {
    var asm = asms.add(i * Process.pointerSize).readPointer();
    var img = asmImage(asm);
    var cnt = 0;
    try { cnt = imageGetClassCount(img); } catch (e) { continue; }
    for (var j = 0; j < cnt; j++) {
      var c = null;
      try { c = imageGetClass(img, j); } catch (e) { continue; }
      if (c.isNull()) continue;
      var nm = "";
      try { nm = className(c).readUtf8String(); } catch (e) { continue; }
      if (nm !== "BattleManagerBase") continue;
      var fields = [];
      var fit = Memory.alloc(Process.pointerSize);
      var f;
      try {
        while (!(f = classGetFields(c, fit)).isNull()) {
          var fnm = "";
          var off = -2;
          var tn = "";
          try { fnm = fieldGetName(f).readUtf8String(); } catch (e) {}
          try { off = fieldGetOffset(f); } catch (e) {}
          try { tn = typeGetName(fieldGetType(f)).readUtf8String(); } catch (e) {}
          fields.push(fnm + " [" + tn + "] off=" + off);
          if (fields.length > 120) break;
        }
      } catch (e) {}
      out.push({cls: nm, fields: fields});
      if (out.length > 20) break;
    }
  }
  send({states: out});
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
    got = []

    def on_message(m, d):
        if m.get("type") != "send":
            return
        p = m.get("payload") or {}
        if isinstance(p.get("states"), list):
            got.extend(p["states"])

    print("attach", pid, "……")
    session = frida.attach(pid)
    script = session.create_script(JS)
    script.on("message", on_message)
    script.load()
    time.sleep(3)
    try:
        script.unload()
    except Exception:
        pass
    try:
        session.detach()
    except Exception:
        pass
    if not got:
        print("没找到 BattleState 类")
        return 2
    for item in got:
        print("== %s ==" % item["cls"])
        for f in item["fields"][:30]:
            print("   ", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())

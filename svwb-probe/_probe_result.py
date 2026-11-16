#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测对局胜负：hook BattleManagerBase.get_IsBattleEnd / JudgeBattleResult。

游戏在对局中时运行：python _probe_result.py
会打印 IsBattleEnd 的返回值，以及 JudgeBattleResult 调用时的参数/返回值。
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

  var domain = domainGet();
  try { threadAttach(domain); } catch (e) {}
  var nPtr = Memory.alloc(4);
  var asms = getAssemblies(domain, nPtr);
  var n = nPtr.readInt();
  var klass = null;
  for (var i = 0; i < n && !klass; i++) {
    var asm = asms.add(i * Process.pointerSize).readPointer();
    var img = asmImage(asm);
    var k = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("BattleManagerBase"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no class"}); return; }
  var it = Memory.alloc(Process.pointerSize);
  var m;
  var hooked = [];
  while (!(m = classGetMethods(klass, it)).isNull()) {
    var nm = "";
    try { nm = methodGetName(m).readUtf8String(); } catch (e) { continue; }
    if (nm !== "get_IsBattleEnd" && nm !== "JudgeBattleResult") continue;
    try {
      var fn = m.readPointer();
      (function (name) {
        Interceptor.attach(fn, {
          onEnter: function (args) {
            this.self = args[0];
            this.name = name;
          },
          onLeave: function (retval) {
            try {
              send({probe: {name: this.name,
                            ret: retval.toString(),
                            retInt: retval.toInt32(),
                            addr: this.self.toString()}});
            } catch (e) {
              send({probe: {name: this.name, err: String(e)}});
            }
          }
        });
      })(nm);
      hooked.push(nm);
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
        print("游戏没在跑")
        return 1
    print("attach", pid, "探测 15 秒……（对局结束前后最关键）")
    session = frida.attach(pid)
    script = session.create_script(JS)
    seen = {}

    def on_message(m, d):
        if m.get("type") != "send":
            return
        p = m.get("payload") or {}
        if "hooked" in p:
            print("hook:", p["hooked"])
        if isinstance(p.get("probe"), dict):
            pr = p["probe"]
            key = (pr.get("name"), pr.get("retInt"))
            if key in seen:
                return
            seen[key] = True
            print(pr)

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

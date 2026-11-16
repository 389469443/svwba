# -*- coding: utf-8 -*-
"""探测当前 Mpo 的费用字段（游戏内费用 1/1 时运行）。"""
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
  var it = Memory.alloc(Process.pointerSize);
  var m, fn = null;
  while (!(m = classGetMethods(klass, it)).isNull()) {
    var nm = "";
    try { nm = methodGetName(m).readUtf8String(); } catch (e) { continue; }
    if (nm === "get_HandCardList") { fn = m.readPointer(); break; }
  }
  if (fn === null) { send({err: "no getter"}); return; }
  var done = false;
  Interceptor.attach(fn, {onEnter: function (args) {
    if (done) return;
    try {
      var p = args[0];
      var cls = "";
      try { cls = className(objClass(p)).readUtf8String(); } catch (e) {}
      if (cls !== "BattleStatePlayerMpo") return;
      done = true;
      var dump = {};
      for (var o = 0x30; o <= 0xb0; o += 4) {
        try { dump["+0x" + o.toString(16)] = p.add(o).readS32(); } catch (e) {}
      }
      send({mpo: {addr: p.toString(), dump: dump}});
    } catch (e) {}
  }});
})();
"""


def main():
    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    if not pid:
        print("游戏没在跑")
        return 1
    print("attach", pid)
    session = frida.attach(pid)
    script = session.create_script(JS)
    got = []

    def on_message(m, d):
        if m.get("type") != "send":
            return
        p = m.get("payload") or {}
        if isinstance(p.get("mpo"), dict):
            got.append(p["mpo"])
            print("MPO", p["mpo"]["addr"])
            print("dump:", p["mpo"]["dump"])

    script.on("message", on_message)
    script.load()
    time.sleep(8)
    try:
        script.unload()
    except Exception:
        pass
    try:
        session.detach()
    except Exception:
        pass
    print("done", len(got))
    return 0


if __name__ == "__main__":
    sys.exit(main())

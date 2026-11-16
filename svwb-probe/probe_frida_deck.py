#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性只读探针：hook get_HandCardList，dump 玩家对象的字段/卡组方法。

用途：确定「卡组明细」在 BattleStatePlayerMpo 上的真实偏移。
不写任何 game 状态，只 Interceptor.attach + 读取。
"""
import sys
import time

import frida

PID = int(sys.argv[1])
WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 25

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
  var classGetFields = P("il2cpp_class_get_fields", "pointer", ["pointer","pointer"]);
  var fieldGetName = P("il2cpp_field_get_name", "pointer", ["pointer"]);
  var fieldGetOffset = P("il2cpp_field_get_offset", "int", ["pointer"]);
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
  var iter = Memory.alloc(Process.pointerSize);
  var fn = null, m;
  while (!(m = classGetMethods(klass, iter)).isNull()) {
    var nm = methodGetName(m).readUtf8String();
    if (nm === "get_HandCardList") { fn = m.readPointer(); break; }
  }
  if (fn === null) { send({err: "no getter"}); return; }

  var dumped = false;
  Interceptor.attach(fn, {
    onEnter: function (args) {
      if (dumped) return;
      var self = args[0];
      var cls = objClass(self);
      var cname = className(cls).readUtf8String();

      var fields = [];
      var fiter = Memory.alloc(Process.pointerSize);
      var f;
      while (!(f = classGetFields(cls, fiter)).isNull()) {
        fields.push(fieldGetOffset(f) + ":" + fieldGetName(f).readUtf8String());
      }

      var methods = [];
      var miter = Memory.alloc(Process.pointerSize);
      var mm;
      while (!(mm = classGetMethods(cls, miter)).isNull()) {
        var mn = methodGetName(mm).readUtf8String();
        if (mn.indexOf("Deck") >= 0 || mn.indexOf("deck") >= 0) methods.push(mn);
      }

      var probes = [];
      [0x08,0x10,0x18,0x20,0x28,0x30,0x38,0x40,0x48,0x50,0x58,0x60,
       0x68,0x70,0x78,0x80,0x88,0x90,0x98,0xA0,0xA8,0xB0,0xB8,0xC0,
       0xC8,0xD0,0xD8,0xE0].forEach(function (off) {
        try {
          var p = self.add(off).readPointer();
          if (p.isNull() || p.compare(0x10000) < 0) return;
          var n2 = p.add(0x18).readS32();
          if (n2 < 0 || n2 > 200) return;
          var ids = [];
          for (var i = 0; i < Math.min(n2, 6); i++) {
            var e = p.add(0x20 + i * 8).readPointer();
            if (e.isNull()) continue;
            var eid = e.add(0xA8).readPointer();
            ids.push(eid.add(0x90).readS32());
          }
          probes.push(off + " size=" + n2 + " ids=" + ids.join(","));
        } catch (e) {}
      });
      send({cls: cname, fields: fields, deck_methods: methods, probes: probes});
      dumped = true;
    }
  });
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

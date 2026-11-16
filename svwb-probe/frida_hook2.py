#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Frida: hook BattlePlayerBase 的 get_HandCardList（对局中手牌变化会调用）。
用法： python -u frida_hook2.py <pid> [seconds]
"""
import sys
import time

import frida

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 10852
WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 90

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
  var fieldFromName = P("il2cpp_class_get_field_from_name", "pointer", ["pointer","pointer"]);
  var fieldGet = P("il2cpp_field_get_value", "void", ["pointer","pointer","pointer"]);
  var objClass = P("il2cpp_object_get_class", "pointer", ["pointer"]);

  var domain = domainGet();
  try { threadAttach(domain); } catch (e) { send({attachErr: String(e)}); }
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

  var iter = Memory.alloc(Process.pointerSize);
  var picked = [];
  var m;
  while (!(m = classGetMethods(klass, iter)).isNull()) {
    var nm = methodGetName(m).readUtf8String();
    if (nm.indexOf("HandCardList") >= 0 || nm.indexOf("DeckCardList") >= 0) {
      picked.push({name: nm, method: m});
    }
  }
  send({picked: picked.map(function (x) { return x.name; })});

  var names = ["<HandCardList>k__BackingField", "<DeckCardList>k__BackingField",
               "<BattleStartDeckCardList>k__BackingField",
               "<CemeteryList>k__BackingField", "<BanishList>k__BackingField"];
  var printed = 0;
  picked.forEach(function (it) {
    var fn = it.method.readPointer();
    if (fn.isNull()) return;
    try {
      Interceptor.attach(fn, {
        onEnter: function (args) {
          if (printed >= 6) return;
          printed++;
          var self = args[0];
          var info = {method: it.name, self: self.toString(), lists: {}};
          try {
            var k = klass;
            names.forEach(function (fname) {
              var fld = fieldFromName(k, Memory.allocUtf8String(fname));
              if (fld.isNull()) { info.lists[fname] = "no field"; return; }
              var lv = Memory.alloc(8);
              fieldGet(self, fld, lv);
              var list = lv.readPointer();
              if (list.isNull()) { info.lists[fname] = null; }
              else {
                var items = list.add(0x10).readPointer();
                info.lists[fname] = {
                  addr: list.toString(),
                  size: list.add(0x18).readS32(),
                  items: items.toString(),
                  max: items.isNull() ? -1 : items.add(0x18).readS32()
                };
              }
            });
          } catch (e) { info.err = String(e); }
          send(info);
        }
      });
    } catch (e) { send({hookErr: String(e)}); }
  });
  send({hooked: picked.length});
})();
"""


def main():
    print("attach", PID, "wait", WAIT)
    session = frida.attach(PID)
    script = session.create_script(JS)
    script.on("message", lambda m, d: print("MSG:", m))
    script.load()
    time.sleep(WAIT)
    try:
        script.unload()
    except Exception:
        pass
    session.detach()


if __name__ == "__main__":
    main()

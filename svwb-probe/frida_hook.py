#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Frida: hook BattlePlayerBase 的构造函数，捕获实例后读 5 个 List（只读）。
用法： python -u frida_hook.py <pid> [seconds]
然后在游戏里开一局（人机即可），脚本会打印捕获到的对象与列表。
"""
import sys
import time

import frida

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 0
WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 120

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
  send({klass: klass ? klass.toString() : "null"});
  if (!klass) return;

  // 遍历方法找 .ctor
  var iter = Memory.alloc(Process.pointerSize);
  var methods = [];
  var m;
  while (!(m = classGetMethods(klass, iter)).isNull()) {
    var nm = methodGetName(m).readUtf8String();
    if (nm === ".ctor") methods.push(m);
  }
  send({ctors: methods.length});
  if (!methods.length) return;

  var captured = 0;
  for (var j = 0; j < methods.length; j++) {
    var fn = methods[j].readPointer();  // MethodInfo.methodPointer
    if (fn.isNull()) continue;
    try {
      Interceptor.attach(fn, {
        onEnter: function (args) {
          if (captured >= 4) return;
          captured++;
          var self = args[0];
          var info = {ctor: self.toString(), lists: {}};
          try {
            var k = objClass(self);
            var names = ["<HandCardList>k__BackingField", "<DeckCardList>k__BackingField",
                         "<BattleStartDeckCardList>k__BackingField",
                         "<CemeteryList>k__BackingField", "<BanishList>k__BackingField"];
            for (var q = 0; q < names.length; q++) {
              var fld = fieldFromName(k, Memory.allocUtf8String(names[q]));
              if (fld.isNull()) { info.lists[names[q]] = "no field"; continue; }
              var lv = Memory.alloc(8);
              fieldGet(self, fld, lv);
              var list = lv.readPointer();
              info.lists[names[q]] = list.isNull() ? null : {
                addr: list.toString(), size: list.add(0x18).readS32()
              };
            }
          } catch (e) { info.err = String(e); }
          send(info);
        }
      });
    } catch (e) { send({attachErr: String(e)}); }
  }
  send({hooked: methods.length});
})();
"""


def main():
    if not PID:
        print("usage: frida_hook.py <pid> [seconds]")
        return
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

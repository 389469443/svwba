#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探针：枚举 BattlePlayerBase 的字段偏移与 Deck/Hand 相关方法名。"""
import sys
import time

import frida

PID = int(sys.argv[1])
WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 10

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
  var className = P("il2cpp_class_get_name", "pointer", ["pointer"]);
  var classParent = P("il2cpp_class_get_parent", "pointer", ["pointer"]);

  var domain = domainGet();
  try { threadAttach(domain); } catch (e) {}
  var nPtr = Memory.alloc(4);
  var asms = getAssemblies(domain, nPtr);
  var n = nPtr.readInt();
  var out = {};
  for (var i = 0; i < n; i++) {
    var asm = asms.add(i * Process.pointerSize).readPointer();
    var img = asmImage(asm);
    ["BattlePlayerBase", "BattleStatePlayerMpo"].forEach(function (name) {
      if (out[name]) return;
      var k = classFromName(img, Memory.allocUtf8String(""),
                            Memory.allocUtf8String(name));
      if (k.isNull()) return;
      var fields = [];
      var fiter = Memory.alloc(Process.pointerSize);
      var f;
      while (!(f = classGetFields(k, fiter)).isNull()) {
        fields.push(fieldGetOffset(f) + ":" + fieldGetName(f).readUtf8String());
      }
      var methods = [];
      var miter = Memory.alloc(Process.pointerSize);
      var mm;
      while (!(mm = classGetMethods(k, miter)).isNull()) {
        methods.push(methodGetName(mm).readUtf8String());
      }
      var parent = classParent(k);
      out[name] = {
        parent: parent.isNull() ? "" : className(parent).readUtf8String(),
        fields: fields,
        methods: methods.filter(function (m) {
          return m.indexOf("Deck") >= 0 || m.indexOf("Hand") >= 0 ||
                 m.indexOf("CardList") >= 0;
        })
      };
    });
  }
  send(out);
})();
"""


def main():
    session = frida.attach(PID)
    script = session.create_script(JS)

    def on_message(m, d):
        print(m, flush=True)

    script.on("message", on_message)
    script.load()
    time.sleep(WAIT)
    session.detach()


if __name__ == "__main__":
    main()

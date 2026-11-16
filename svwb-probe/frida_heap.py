#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frida: 遍历 GC 堆找 BattlePlayerBase（带 try/catch，异步）。"""
import sys
import time

import frida

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 20484

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
  var objClass = P("il2cpp_object_get_class", "pointer", ["pointer"]);
  var isAssignable = P("il2cpp_class_is_assignable_from", "bool", ["pointer","pointer"]);
  var foreachHeap = P("il2cpp_gc_foreach_heap", "void", ["pointer","pointer"]);
  var fieldFromName = P("il2cpp_class_get_field_from_name", "pointer", ["pointer","pointer"]);
  var fieldGet = P("il2cpp_field_get_value", "void", ["pointer","pointer","pointer"]);
  var classParent = P("il2cpp_class_get_parent", "pointer", ["pointer"]);

  var domain = domainGet();
  try { threadAttach(domain); } catch (e) { send({attachErr: String(e)}); }
  var nPtr = Memory.alloc(4);
  var asms = getAssemblies(domain, nPtr);
  var n = nPtr.readInt();
  var target = null;
  for (var i = 0; i < n && !target; i++) {
    var asm = asms.add(i * Process.pointerSize).readPointer();
    var img = asmImage(asm);
    var k = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("BattlePlayerBase"));
    if (!k.isNull()) target = k;
  }
  send({target: target ? target.toString() : "null"});
  if (!target) return;

  setTimeout(function () {
    var found = [];
    var errs = 0;
    var cb = new NativeCallback(function (obj, user) {
      if (obj.isNull()) return;
      try {
        var k = objClass(obj);
        if (isAssignable(target, k)) found.push(obj);
      } catch (e) { errs++; }
    }, "void", ["pointer", "pointer"]);
    foreachHeap(cb, ptr(0));
    send({found: found.length, errs: errs});

    var names = ["<HandCardList>k__BackingField", "<DeckCardList>k__BackingField",
                 "<BattleStartDeckCardList>k__BackingField",
                 "<CemeteryList>k__BackingField", "<BanishList>k__BackingField"];
    for (var j = 0; j < Math.min(found.length, 4); j++) {
      var obj = found[j];
      var k = objClass(obj);
      var info = {index: j, addr: obj.toString(), lists: {}};
      for (var q = 0; q < names.length; q++) {
        try {
          var fld = fieldFromName(k, Memory.allocUtf8String(names[q]));
          if (fld.isNull()) { info.lists[names[q]] = "no field"; continue; }
          var lv = Memory.alloc(8);
          fieldGet(obj, fld, lv);
          var list = lv.readPointer();
          info.lists[names[q]] = list.isNull() ? null : {
            addr: list.toString(), size: list.add(0x18).readS32()
          };
        } catch (e) { info.lists[names[q]] = "err " + e; }
      }
      send(info);
    }
  }, 100);
})();
"""


def main():
    print("attach", PID)
    session = frida.attach(PID)
    script = session.create_script(JS)
    script.on("message", lambda m, d: print("MSG:", m))
    script.load()
    time.sleep(8)
    try:
        script.unload()
    except Exception:
        pass
    session.detach()


if __name__ == "__main__":
    main()

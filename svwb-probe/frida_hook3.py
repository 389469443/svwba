#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frida: 按真实偏移直接读 BattlePlayerBase 的 5 个 List（不经过 fieldGet）。"""
import sys
import time

import frida

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 10852
WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 90

OFF = {"hand": 160, "deck": 168, "start": 176, "cemetery": 200, "banish": 208}

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
    var k = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("BattlePlayerBase"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no class"}); return; }

  var iter = Memory.alloc(Process.pointerSize);
  var fns = {};
  var m;
  while (!(m = classGetMethods(klass, iter)).isNull()) {
    var nm = methodGetName(m).readUtf8String();
    if (nm === "get_HandCardList" || nm === "get_DeckCardList" ||
        nm === "get_BattleStartDeckCardList" || nm === "get_CemeteryList" ||
        nm === "get_BanishList") {
      fns[nm] = m.readPointer();
    }
  }
  send({methods: Object.keys(fns)});

  var offs = {hand: 160, deck: 168, start: 176, cemetery: 200, banish: 208};
  var printed = 0;
  Object.keys(fns).forEach(function (nm) {
    var fn = fns[nm];
    if (fn.isNull()) return;
    try {
      Interceptor.attach(fn, {
        onEnter: function (args) {
          if (printed >= 8) return;
          printed++;
          var self = args[0];
          var info = {method: nm, self: self.toString(), lists: {}};
          Object.keys(offs).forEach(function (key) {
            try {
              var list = self.add(offs[key]).readPointer();
              if (list.isNull()) { info.lists[key] = null; return; }
              var items = list.add(0x10).readPointer();
              info.lists[key] = {
                addr: list.toString(),
                size: list.add(0x18).readS32(),
                items: items.toString(),
                max: items.isNull() ? -1 : items.add(0x18).readS32()
              };
            } catch (e) { info.lists[key] = "err"; }
          });
          send(info);
        }
      });
    } catch (e) { send({hookErr: String(e)}); }
  });
  send({hooked: Object.keys(fns).length});
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

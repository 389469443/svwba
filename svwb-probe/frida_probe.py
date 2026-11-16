#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frida attach 游戏，列 IL2CPP 导出（只读）。2026-10-21"""
import sys
import time

import frida

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 20484

JS = r"""
(function () {
  var ga = Process.findModuleByName("GameAssembly.dll");
  if (!ga) { send({ok:false, err:"GameAssembly not found"}); return; }
  send({ok:true, base: ga.base.toString(), size: ga.size});
  var names = [];
  try {
    names = ga.enumerateExports()
      .filter(function (e) { return e.name.indexOf("il2cpp_") === 0; })
      .map(function (e) { return e.name; });
  } catch (e) { send({ok:false, err:String(e)}); return; }
  send({ok:true, count: names.length, exports: names.slice(0, 40)});
})();
"""


def main():
    print("attach", PID)
    session = frida.attach(PID)
    script = session.create_script(JS)
    msgs = []

    def on_message(msg, data):
        print("MSG:", msg)
        msgs.append(msg)

    script.on("message", on_message)
    script.load()
    time.sleep(2)
    print("done, msgs:", len(msgs))
    try:
        script.unload()
    except Exception:
        pass
    session.detach()


if __name__ == "__main__":
    main()

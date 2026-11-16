#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探针：扫游戏进程内存里的 JSON 文本（cardId / deckId），找 DeckDB 响应残留。"""
import sys
import time

import frida

PID = int(sys.argv[1])
WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 90

JS = r"""
(function () {
  function hex(s) {
    var out = [];
    for (var i = 0; i < s.length; i++) {
      out.push(("0" + s.charCodeAt(i).toString(16)).slice(-2));
    }
    return out.join(" ");
  }
  var pats = [["cardId", hex("cardId")], ["deckId", hex("deckId")],
              ["cardIdList", hex("cardIdList")],
              ["DeckGetList", hex("DeckGetList")]];
  var done = false;

  function scan() {
    if (done) return;
    done = true;
    var ranges = Process.enumerateRanges("rw-");
    var hits = 0;
    for (var p = 0; p < pats.length; p++) {
      var name = pats[p][0], pattern = pats[p][1];
      var found = [];
      ranges.forEach(function (r) {
        try {
          if (r.size <= 0x1000 || r.size > 0x40000000 || r.file) return;
          var res = Memory.scanSync(r.base, r.size, pattern);
          for (var i = 0; i < res.length && found.length < 5; i++) {
            found.push(res[i].address);
          }
        } catch (e) {}
      });
      if (found.length) {
        hits += found.length;
        found.forEach(function (addr) {
          try {
            var buf = addr.sub(256).readByteArray(1024);
            var bytes = new Uint8Array(buf);
            var text = "";
            for (var j = 0; j < bytes.length; j++) {
              var b = bytes[j];
              text += (b >= 32 && b < 127) ? String.fromCharCode(b) : ".";
            }
            send({pat: name, addr: addr.toString(), text: text});
          } catch (e) {}
        });
      }
    }
    send({done: true, hits: hits});
  }
  setTimeout(scan, 100);
})();
"""


def main():
    session = frida.attach(PID)
    script = session.create_script(JS)

    def on_message(m, d):
        if m.get("type") != "send":
            return
        p = m.get("payload") or {}
        if p.get("done"):
            print("== scan done, hits:", p.get("hits"), flush=True)
            return
        print("== %s @ %s" % (p.get("pat"), p.get("addr")), flush=True)
        print(p.get("text"), flush=True)
        print(flush=True)

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

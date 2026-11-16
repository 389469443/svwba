#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开局卡组捕获器（常驻）。

思路：hook BattlePlayerBase 的卡组 setter（HybridCLR 解释器桩也能命中原调用对象），
命中时异步扫堆找 BattlePlayerBase 实例 → 读 +0xB0 BattleStartDeckCardList →
写 gamedata/user_deck.json 给记牌器用。

优雅停：写 logs/_frida.stop，主循环看到就 unload 退出（不 taskkill）。
"""
import json
import os
import sys
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
STOP = os.path.join(HERE, "logs", "_frida.stop")
OUT = os.path.join(HERE, "gamedata", "user_deck.json")
LOG = os.path.join(HERE, "logs", "deck_capture.log")

PID = int(sys.argv[1]) if len(sys.argv) > 1 else 0
WAIT = int(sys.argv[2]) if len(sys.argv) > 2 else 7200

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
    var k = classFromName(img, Memory.allocUtf8String(""),
                          Memory.allocUtf8String("BattlePlayerBase"));
    if (!k.isNull()) klass = k;
  }
  if (!klass) { send({err: "no class"}); return; }

  var hex = klass.toString(16).replace(/^0x/, "");
  while (hex.length < 16) hex = "0" + hex;
  var bytes = [];
  for (var i = hex.length - 2; i >= 0; i -= 2) bytes.push(hex.substr(i, 2));
  var pattern = bytes.join(" ");

  function dumpDeck(obj) {
    try {
      var p = obj.add(0xB0).readPointer();
      if (p.isNull()) return null;
      var cn = "?";
      try { cn = className(objClass(p)).readUtf8String(); } catch (e) {}
      var len = -1;
      try { len = p.add(0x18).readS32(); } catch (e) { return null; }
      if (cn.indexOf("[]") < 0 || len <= 0 || len > 60) return null;
      var ids = [];
      for (var i = 0; i < len; i++) {
        var e = p.add(0x20 + i * 8).readPointer();
        if (e.isNull()) { ids.push(0); continue; }
        var cid = -1;
        try { cid = e.add(0xA8).readPointer().add(0x90).readS32(); } catch (e2) {}
        ids.push(cid);
      }
      return ids;
    } catch (e) { return null; }
  }

  function scanOnce() {
    var found = [];
    var ranges = Process.enumerateRanges("rw-");
    ranges.forEach(function (r) {
      try {
        if (r.size <= 0x1000 || r.size > 0x40000000 || r.file) return;
        var res = Memory.scanSync(r.base, r.size, pattern);
        for (var i = 0; i < res.length; i++) {
          var addr = res[i].address;
          try {
            if (!addr.readPointer().equals(klass)) continue;
            if (!addr.add(8).readPointer().isNull()) continue;   // monitor
            var f = addr.add(0x10).readU8();
            if (f !== 0 && f !== 1) continue;
            var ids = dumpDeck(addr);
            if (ids && ids.length > 20) found.push(ids);
          } catch (e) {}
        }
      } catch (e) {}
    });
    if (found.length) send({deck_ids: found[0]});
    else send({deck_ids: null});
  }

  var triggers = {};
  var miter = Memory.alloc(Process.pointerSize);
  var mm;
  while (!(mm = classGetMethods(klass, miter)).isNull()) {
    var mn = methodGetName(mm).readUtf8String();
    if (mn === "set_BattleStartDeckCardList" || mn === "set_HandCardList" ||
        mn === "set_DeckCardList") {
      triggers[mn] = mm.readPointer();
    }
  }

  var scanning = false;
  Object.keys(triggers).forEach(function (name) {
    try {
      Interceptor.attach(triggers[name], {
        onEnter: function () {
          if (scanning) return;
          scanning = true;
          send({trigger: name});
          setTimeout(function () { scanOnce(); scanning = false; }, 500);
        }
      });
    } catch (e) {}
  });
  send({hooked: Object.keys(triggers)});
})();
"""


def main():
    pid = PID
    if pid <= 0:
        try:
            import shadow_warchess_reader as RW
            pid, _ = RW.find_game_pid()
        except Exception:
            pid = 0
    if not pid:
        print("没找到游戏进程", flush=True)
        return 1
    try:
        import singleton
        if not singleton.acquire("SvwbDeckCapture"):
            print("已有捕获器在跑", flush=True)
            return 0
    except Exception:
        pass
    try:
        os.remove(STOP)
    except Exception:
        pass

    session = frida.attach(pid)
    script = session.create_script(JS)

    def on_message(m, d):
        if m.get("type") != "send":
            return
        p = m.get("payload") or {}
        if "deck_ids" in p:
            ids = p.get("deck_ids")
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write("%s deck_ids=%s\n"
                         % (time.strftime("%H:%M:%S"),
                            "None" if ids is None else str(len(ids))))
            if ids:
                os.makedirs(os.path.dirname(OUT), exist_ok=True)
                with open(OUT, "w", encoding="utf-8") as fh:
                    json.dump({"cards": ids, "source": "frida_capture",
                               "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                              fh, ensure_ascii=False)
                print("抓到卡组 %d 张 → %s" % (len(ids), OUT), flush=True)
        else:
            print(m, flush=True)

    script.on("message", on_message)
    script.load()
    t0 = time.time()
    try:
        while time.time() - t0 < WAIT:
            if os.path.exists(STOP):
                print("收到停止请求", flush=True)
                break
            time.sleep(1)
    finally:
        try:
            script.unload()
        except Exception:
            pass
        try:
            session.detach()
        except Exception:
            pass
        try:
            os.remove(STOP)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

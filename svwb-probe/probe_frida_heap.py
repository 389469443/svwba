#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读探针 B：在游戏进程堆里扫描 BattlePlayerBase 实例（对象头 = klass 指针），
找到后读 +0xB0 BattleStartDeckCardList（卡组 40 张）。

不依赖方法 hook（绕开 HybridCLR 解释器桩），纯内存读。
"""
import sys
import time

import frida

PID = int(sys.argv[1])
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
  send({klass: klass.toString()});

  // 小端字节模式
  var hex = klass.toString(16).replace(/^0x/, "");
  while (hex.length < 16) hex = "0" + hex;
  var bytes = [];
  for (var i = hex.length - 2; i >= 0; i -= 2) bytes.push(hex.substr(i, 2));
  var pattern = bytes.join(" ");

  function dumpDeck(obj) {
    try {
      var p = obj.add(0xB0).readPointer();
      if (p.isNull()) return "null";
      var cn = "?";
      try { cn = className(objClass(p)).readUtf8String(); } catch (e) {}
      var len = -1;
      try { len = p.add(0x18).readS32(); } catch (e) {}
      var ids = [];
      if (cn.indexOf("[]") >= 0 && len > 0 && len < 200) {
        for (var i = 0; i < Math.min(len, 8); i++) {
          var e = p.add(0x20 + i * 8).readPointer();
          if (e.isNull()) { ids.push("null"); continue; }
          var ecls = "?";
          try { ecls = className(objClass(e)).readUtf8String(); } catch (e2) {}
          var cid = -1;
          try { cid = e.add(0xA8).readPointer().add(0x90).readS32(); } catch (e3) {}
          ids.push(ecls + ":" + cid);
        }
      }
      return cn + " len=" + len + " [" + ids.join(" | ") + "]";
    } catch (e) { return "err " + e; }
  }

  var ranges = Process.enumerateRanges("rw-");
  var found = [];
  var scanned = 0;

  function doScan() {
    ranges.forEach(function (r) {
      try {
        if (r.size <= 0x1000 || r.size > 0x40000000) return;
        if (r.file) return;                    // 跳过文件映射（模块/资源）
        var res = Memory.scanSync(r.base, r.size, pattern);
        scanned += r.size;
        for (var i = 0; i < res.length; i++) {
          if (found.length >= 8) break;
          found.push(res[i].address);
        }
      } catch (e) {}
    });
    send({scanned_mb: Math.round(scanned / 1048576), hits: found.length});
    found.forEach(function (addr) {
      try {
        // 对象头：addr+0 是 klass；+8 monitor 应 null；+0x10 isLogPlayer 0/1
        if (!addr.readPointer().equals(klass)) return;
        var mon = addr.add(8).readPointer();
        if (!mon.isNull()) return;
        var first = addr.add(0x10).readU8();
        if (first !== 0 && first !== 1) return;
        send({obj: addr.toString(), mon: mon.toString(),
              first: first, deck: dumpDeck(addr)});
      } catch (e) {}
    });
  }
  setTimeout(doScan, 100);
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

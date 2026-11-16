#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frida: 通过 IL2CPP API 找 GameMgr 单例 -> BattlePlayer -> 5 个 List（只读）。"""
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
var getAssemblies = P("il2cpp_domain_get_assemblies", "pointer", ["pointer", "pointer"]);
var asmImage = P("il2cpp_assembly_get_image", "pointer", ["pointer"]);
var classFromName = P("il2cpp_class_from_name", "pointer", ["pointer", "pointer", "pointer"]);
var fieldFromName = P("il2cpp_class_get_field_from_name", "pointer", ["pointer", "pointer"]);
var staticGet = P("il2cpp_field_static_get_value", "void", ["pointer", "pointer"]);
var fieldGet = P("il2cpp_field_get_value", "void", ["pointer", "pointer", "pointer"]);
var objClass = P("il2cpp_object_get_class", "pointer", ["pointer"]);
var classParent = P("il2cpp_class_get_parent", "pointer", ["pointer"]);
var className = P("il2cpp_class_get_name", "pointer", ["pointer"]);
var imageName = P("il2cpp_image_get_name", "pointer", ["pointer"]);

function s(p) { try { return p.readUtf8String(); } catch (e) { return ""; } }
function u64(p) { return p.readPointer(); }

var domain = domainGet();
  try { threadAttach(domain); } catch (e) { send({attachErr: String(e)}); }
var nPtr = Memory.alloc(4);
var asms = getAssemblies(domain, nPtr);
var n = nPtr.readInt();
send({assemblies: n});

var found = 0;
for (var i = 0; i < n; i++) {
  var asm = asms.add(i * Process.pointerSize).readPointer();
  var img = asmImage(asm);
  var klass = classFromName(img, Memory.allocUtf8String(""), Memory.allocUtf8String("GameMgr"));
  if (klass.isNull()) continue;
  found = 1;
  var f = fieldFromName(klass, Memory.allocUtf8String("_instance"));
  var out = Memory.alloc(8);
  staticGet(f, out);
  var inst = out.readPointer();
  send({ok: true, asmIndex: i, image: s(imageName(img)), klass: klass.toString(), instance: inst.toString()});
  if (inst.isNull()) continue;

  var bmF = fieldFromName(klass, Memory.allocUtf8String("_battleMgr"));
  var pb = Memory.alloc(8);
  fieldGet(inst, bmF, pb);
  var bm = pb.readPointer();
  send({battleMgr: bm.toString()});
  if (bm.isNull()) continue;

  var bmKlass = objClass(bm);
  var plF = fieldFromName(bmKlass, Memory.allocUtf8String("BattlePlayer"));
  if (plF.isNull()) {
    // 可能在基类
    var par = classParent(bmKlass);
    while (!par.isNull() && plF.isNull()) {
      plF = fieldFromName(par, Memory.allocUtf8String("BattlePlayer"));
      par = classParent(par);
    }
  }
  var pp = Memory.alloc(8);
  fieldGet(bm, plF, pp);
  var pl = pp.readPointer();
  send({player: pl.toString()});
  if (pl.isNull()) continue;

  var plKlass = objClass(pl);
  var names = ["<HandCardList>k__BackingField", "<DeckCardList>k__BackingField",
               "<BattleStartDeckCardList>k__BackingField",
               "<CemeteryList>k__BackingField", "<BanishList>k__BackingField"];
  for (var j = 0; j < names.length; j++) {
    var fld = fieldFromName(plKlass, Memory.allocUtf8String(names[j]));
    if (fld.isNull()) { send({list: names[j], err: "no field"}); continue; }
    var lv = Memory.alloc(8);
    fieldGet(pl, fld, lv);
    var list = lv.readPointer();
    var size = list.isNull() ? -1 : list.add(0x18).readS32();
    send({list: names[j], addr: list.toString(), size: size});
  }
}
if (!found) send({ok: false, err: "GameMgr class not found"});
})();
"""


def on_message(msg, data):
    print("MSG:", msg)


def main():
    print("attach", PID)
    session = frida.attach(PID)
    script = session.create_script(JS)
    script.on("message", on_message)
    script.load()
    time.sleep(3)
    try:
        script.unload()
    except Exception:
        pass
    session.detach()


if __name__ == "__main__":
    main()

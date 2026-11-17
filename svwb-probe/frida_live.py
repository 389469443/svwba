#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Frida 常驻：hook get_HandCardList，节流读 BattleStatePlayerMpo 状态并写 JSON。"""
import json
import os
import sys
import threading
import time

import frida

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "logs", "deck_live.json")
# 对局结束落盘：{at, me_life, enemy_life}，由 deck_tracker 消费并记一次胜负
END_PATH = os.path.join(HERE, "logs", "battle_end.json")
# 控制台要求停止时写这个文件；主循环看到就卸载 hook。
# 以前只能被 taskkill 强杀 → agent 留在游戏进程里，GC 直接崩。
STOP_FILE = os.path.join(HERE, "logs", "_frida.stop")
DEFAULT_WAIT = 7200


JS = r"""(function () {
  // 主程序在 create_script 之前把 __DECK_IDS__ 替换成导入卡组的卡 id 数组。
  // 为什么不用 RPC 下推：RPC 在游戏进程里注册失败过一次（unable to find
  // method 'setDeck'），而身份判定完全依赖它 —— 一失败就会出现「我方」
  // 和「对手」每帧互换，手牌乱跳、胜负也判不出来。直接嵌进脚本里最稳。
  var DECK_IDS = __DECK_IDS__;
  // 诊断开关（logs/_diag_fields.flag 存在时为 true）：把「对战/结算」相关类的
  // 字段名全 dump 到日志，用来找「这一局到底谁赢了」这类字段，跑一次就够。
  var DIAG_FIELDS = __DIAG_FIELDS__;
  // 卡牌对象诊断（2026-11-17，logs/_diag_card.flag 存在时为 true）：
  // 目的只有一个 —— 找出「这张随从进化了没有」。光看身材 +2/+2 会被
  // 「徒姬超进化给其他梦魇随从 +2/+2」骗过去，必须读卡对象自己的状态位。
  // 打开后每秒把场上每张卡的 (cardId, atk, life, 类名, 0x00~0x60 原始字节)
  // 落到 logs/_cards.txt；对比「进化过的卡」和「被 buff 的卡」就能找出那个字节。
  var DIAG_CARD = __DIAG_CARD__;
  var CARD_CLS = {};       // 类名 -> 反射出来的字段清单（每个类只做一次）
  var cardDumpAt = 0;

  var ga = Process.findModuleByName("GameAssembly.dll");
  if (ga === null) {
    // 进程刚起来、或者这还是模拟器的启动器而不是真正的游戏：
    // 以前这里直接往下走，`ga.findExportByName` 抛 TypeError，整个脚本一句都
    // 没跑起来（日志里那条 SCRIPT ERROR），控制台把 pid 记成「已挂过」，
    // 之后就再也读不到。现在明确报一句，让 Python 侧过一会儿重来。
    send({noModule: true, err: "GameAssembly.dll 还没加载"});
    return;
  }
  function P(n, ret, args) {
    var a = ga.findExportByName(n);
    if (a === null) throw new Error("missing " + n);
    return new NativeFunction(a, ret, args);
  }
  var domainGet = P("il2cpp_domain_get", "pointer", []);
  var threadAttach = P("il2cpp_thread_attach", "pointer", ["pointer"]);
  // 用完必须摘掉，理由见下面「GC」那段注释
  var threadDetach = null;
  try { threadDetach = P("il2cpp_thread_detach", "void", ["pointer"]); } catch (e) {}
  var getAssemblies = P("il2cpp_domain_get_assemblies", "pointer", ["pointer","pointer"]);
  var asmImage = P("il2cpp_assembly_get_image", "pointer", ["pointer"]);
  var classFromName = P("il2cpp_class_from_name", "pointer", ["pointer","pointer","pointer"]);
  var classGetMethods = P("il2cpp_class_get_methods", "pointer", ["pointer","pointer"]);
  var methodGetName = P("il2cpp_method_get_name", "pointer", ["pointer"]);
  var objClass = P("il2cpp_object_get_class", "pointer", ["pointer"]);
  var classGetName = P("il2cpp_class_get_name", "pointer", ["pointer"]);

  // ---- 字段名反射（可选能力）--------------------------------------------
  // 以前 PP / 已打出 / 墓地全靠硬编码偏移猜，PP 猜错了于是界面永远显示 5/5，
  // 对手的列表也一直是空的。Unity IL2CPP 支持按名字取字段偏移，拿到就按名字读，
  // 拿不到再退回旧的硬编码值 —— 少一个导出也不至于把整个探针搞挂。
  var classGetFields = null, fieldGetName = null, fieldGetOffsetFn = null,
      classGetParent = null, fieldGetFlags = null;
  try {
    classGetFields = P("il2cpp_class_get_fields", "pointer", ["pointer", "pointer"]);
    fieldGetName = P("il2cpp_field_get_name", "pointer", ["pointer"]);
    fieldGetOffsetFn = P("il2cpp_field_get_offset", "int", ["pointer"]);
    classGetParent = P("il2cpp_class_get_parent", "pointer", ["pointer"]);
    fieldGetFlags = P("il2cpp_field_get_flags", "uint32", ["pointer"]);
  } catch (e) {
    classGetFields = null;
  }
  var domain = domainGet();
  // ⚠️ 这里只「临时」把 Frida 的 JS 线程接进 IL2CPP 运行时，初始化做完立刻摘掉。
  //
  // 为什么非摘不可（2026-11-07 定位到的卡死真因）：
  //   il2cpp_thread_attach 会把 JS 线程登记进 Unity 的 Boehm GC 线程表。这个
  //   线程永远不跑托管代码，也永远不会响应 GC 的 stop-the-world 请求。于是游戏
  //   做一次「全量 GC」时（**退出流程里必然有最后一次**），GC 会一直等这个线程
  //   到达安全点 —— 等不到，全场线程一起停下。用户看到的就是：点关闭 → 窗口
  //   「未响应」→ CPU 0% → 只能强杀。
  //   实测佐证：卡死时 120 个线程个个挂起计数 0、两秒内没有任何线程消耗 CPU，
  //   而我们的 JS 线程还在正常发 idle 心跳 —— 典型的「全线程阻塞」，不是死循环。
  var curThread = null;
  function threadOn() {
    if (curThread) return;
    try { curThread = threadAttach(domain); } catch (e) {}
  }
  function threadOff() {
    if (!curThread) return;
    try { if (threadDetach) threadDetach(curThread); } catch (e) {}
    curThread = null;
  }
  threadOn();
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
  var fn = null, deckFn = null, m;
  // 所有 get_*CardList（手牌 / 牌库 / 场面 / 已打出 …）：谁的列表被取，谁就被登记成候选。
  // 对手的场面是公开信息，游戏每帧都会取，靠它才能稳定发现「对手那个对象」。
  var getters = [];
  while (!(m = classGetMethods(klass, iter)).isNull()) {
    var nm = methodGetName(m).readUtf8String();
    if (nm === "get_HandCardList") fn = m.readPointer();
    if (nm === "get_DeckCardList") deckFn = m.readPointer();
    if (/^get_[A-Za-z]*CardList$/.test(nm)) getters.push(m.readPointer());
  }
  if (fn === null) { send({err: "no getter"}); return; }

  // ---- 诊断 dump（可用时）------------------------------------------------
  // 完全只读元数据，不需要对局中；输出一次就够，用来查「谁赢了」的字段。
  function dumpClasses() {
    if (!classGetFields) return 0;
    var cnts, cnt, out = 0, i, j;
    try {
      var imgClassCount = P("il2cpp_image_get_class_count", "uint32", ["pointer"]);
      var imgClass = P("il2cpp_image_get_class", "pointer", ["pointer","uint32"]);
      var fieldType = P("il2cpp_field_get_type", "pointer", ["pointer"]);
      var typeName = P("il2cpp_type_get_name", "pointer", ["pointer"]);
    } catch (e) { return -1; }
    for (i = 0; i < n && out < 70; i++) {
      var im = asmImage(asms.add(i * Process.pointerSize).readPointer());
      try { cnt = imgClassCount(im); } catch (e) { continue; }
      for (j = 0; j < cnt && out < 70; j++) {
        var ck;
        try { ck = imgClass(im, j); } catch (e) { continue; }
        if (ck.isNull()) continue;
        var cn = "";
        try { cn = classGetName(ck).readUtf8String() || ""; } catch (e) { continue; }
        if (!/Battle|Result|Victory|Defeat|GameEnd/i.test(cn)) continue;
        if (/Windows?/i.test(cn)) continue;      // Window / Windows 之类误伤
        var fs = [], cur = ck, dep = 0;
        while (!cur.isNull() && dep++ < 5) {
          var it2 = Memory.alloc(Process.pointerSize), f2;
          while (!(f2 = classGetFields(cur, it2)).isNull()) {
            var fnm = "", ftp = "";
            try { fnm = fieldGetName(f2).readUtf8String() || ""; } catch (e) {}
            try { ftp = typeName(fieldType(f2)).readUtf8String() || ""; } catch (e) {}
            fs.push("+0x" + fieldGetOffsetFn(f2).toString(16) + " " + fnm + " : " + ftp);
          }
          cur = classGetParent(cur);
        }
        if (!fs.length) continue;
        out++;
        send({dump: cn, fields: fs});
      }
    }
    return out;
  }
  if (DIAG_FIELDS) {
    try { send({dump_done: dumpClasses()}); }
    catch (e) { send({err: "dump: " + e}); }
  }

  var stopped = false;
  // 游戏正在退出：关游戏时所有 hook / 定时器都要立刻停手，否则会卡住退出流程
  // （见下面 ExitProcess 那段注释）
  var quitting = false;
  // 候选玩家对象池：[{p, ts}]，ts = 最后一次被 getter 触碰的时间。
  // 游戏会不停新建 BattleStatePlayerMpo（每次状态同步一份），旧对象即使被 GC，
  // 内存往往还能读出完整字段 —— 只看「能不能读」会把僵尸对象当成活人，
  // 所以必须靠 ts 区分：只有最近还被取过列表的才算当前玩家。
  var cands = [];
  var deckSet = {}, deckSetN = 0;
  var lastIdle = 0;
  var lastMe = null, lastEnemy = null, lastEnemyAddr = null;
  var poolWarn = false;   // 「只读得到对手」的告警只打一次，别刷屏
  var meMin = null, enemyMin = null;
  var overDone = false;    // 本局是否已经发过「对局结束」（防止一局记多次）
  var overWhy = "";        // 上一条结束事件的原因（dead / next / gone）
  // 这一条是不是「对象被销毁」这种硬结束。硬结束不许被下面的
  // 「我方的数据又回来了」撤销 —— 否则新局一开就会再记一次（重复记账）。
  var overDoneHard = false;
  // 本局内是否见过「活着」的血量。用来防止把上一局遗留对象读到的 0 血
  // 误当成这一局的死亡（换局瞬间两局对象会短暂共存）。
  var meAliveSeen = false, enemyAliveSeen = false;
  var FRESH_MS = 4000;     // 4 秒内没被 getter 碰过 → 不当活对象
  var KEEP_MS = 20000;     // 20 秒后才彻底从池子里丢掉
  // ---- 盲区处理（用户 2026-11-06：对局中记牌器偶尔跳回「当前卡组」预览）----
  // 游戏的状态对象是「每次同步一份」，长考期间（无论我方还是对手回合）可能连续
  // 十几秒读不出合法字段。
  // 现在：读不到我方就**补发上一帧**（保持 deck_live.json 新鲜，界面不切视图），
  // 而**不再**因此发「对局结束」—— 读不到和对局结束是两件事。
  var lastInfo = null;     // 最后一帧读成功的状态（盲区时用它补发）
  var blindSince = 0;      // 从什么时候开始读不到我方
  var blindN = 0;          // 连续「认不出我方」的帧数
  var weakMode = false;    // 已在退化模式（没有可用卡组时才会进）
  var holdN = 0;           // 连续补发次数（只为控制日志）
  // 读不到这么久 → 发一条 gone 兜底。阈值一路放宽的经过：
  //   8 秒（最初）→ 25 秒（08:4x 长考被切成十几局）→ 45 秒（12:5x 斩杀回合
  //   思考 40 秒以上又中一次）→ 90 秒（用户 2026-11-06 定稿）。
  // 长考和人真的走掉，本来只能靠时间区分，宁可多等；真结束了会先走
  // dead / next 两条硬信号，轮不到这条。
  var BLIND_END_MS = 90000;
  // gone 之后再等这么久才发 idle（切回「当前卡组」预览）。
  // 这段的作用是「长考回来还能接上」：gone 只是兜底判断，人可能还在想。
  var BLIND_GRACE_MS = 60000;
  // hold 的上限 = 上面两段之和。超过就回预览，不记胜负。
  var BLIND_IDLE_MS = BLIND_END_MS + BLIND_GRACE_MS;
  // 「一个候选对象都读不到」和「长考」不是一回事：
  //   长考时对象还在，只是字段不变（nCand > 0），只能靠时间熬；
  //   投降 / 点「返回」之后对象被销毁，nCand 会**持续为 0**。
  // 后者不必等 90 秒 —— 这个时长就是「投降后多久把账记上」。
  //   8 秒：记账快，但对方长考时（对象被销毁重建）容易被打断 ——
  //         2026-11-11 下午真实踩到过，满血局面被误判成结束；
  //   15 秒：多等 7 秒，换对方长考时更不容易误报（用户 2026-11-11 选定）。
  // 注：投降后如果用户很快开了下一局，走的是 isNewGame → next 那条路，
  //     和这个窗口无关，所以调大不会丢账。
  var HARD_END_MS = 15000;
  var hardSince = 0;       // 「连续一个候选都没有」的起点（毫秒）
  var goneSent = false;    // 本次盲区是否已经发过 gone
  // 本局流水号：每换一局 +1，跟着每条消息发给主程序。主程序只按它去重，
  // 这样就算探针多发了结束事件，一局也只会记一次胜负。
  // 加一个「脚本启动时刻」当基数：探针重启（挂机 2 小时会重启）后流水号
  // 不会和上一轮撞车，否则重启后的第一局会被主程序当成「已经记过」。
  var SEQ_BASE = Math.floor(Date.now() / 1000) * 1000;
  var battleSeq = 0;
  function seqNo() { return SEQ_BASE + battleSeq; }

  function setDeck(ids) {
    deckSet = {}; deckSetN = 0;
    try {
      for (var i = 0; i < ids.length; i++) {
        deckSet["" + ids[i]] = 1;
        deckSetN++;
      }
    } catch (e) {}
    return deckSetN;
  }
  setDeck(DECK_IDS || []);

  function clsName(p) {
    try { return classGetName(objClass(p)).readUtf8String() || ""; }
    catch (e) { return ""; }
  }
  function isPlayerState(p) {
    try {
      if (p.isNull()) return false;
      // 人机 / 剧情模式里对手可能不是 BattleStatePlayerMpo，放宽到 *PlayerMpo
      return /PlayerMpo/.test(clsName(p));
    } catch (e) { return false; }
  }
  function noteCand(p) {
    if (stopped || quitting || p.isNull()) return;
    var now = Date.now();
    for (var i = 0; i < cands.length; i++) {
      if (cands[i].p.equals(p)) { cands[i].ts = now; return; }
    }
    cands.push({p: p, ts: now});
    if (cands.length > 12) cands.shift();
  }
  function readArr(arr, kind) {
    if (arr.isNull()) return [];
    var cnt = arr.add(0x18).readS32();
    if (cnt < 0 || cnt > 60) return [];
    var out = [];
    for (var i = 0; i < Math.min(cnt, 45); i++) {
      try {
        if (kind === "hand" || kind === "field") {
          var e = arr.add(0x20 + i * 8).readPointer();
          if (e.isNull()) continue;
          if (kind === "hand") out.push(e.add(0x14).readS32());
          else out.push({cardId: e.add(0x14).readS32(),
                         // 实例编号（<UniqueId>@0x10，4 字节）：同名两张牌靠它
                         // 才能分开 —— 进化事件按实例记，不能按卡 id 记。
                         uid: e.add(0x10).readS32(),
                         atk: e.add(0x1C).readS32(),
                         life: e.add(0x20).readS32(),
                         // 进化状态（2026-11-17 实测确认）：BattleFieldCardMpo
                         // 的 <EvolveState>k__BackingField @0x40
                         //   0 = 未进化  1 = 进化  2 = 超进化
                         // 这条比「身材 +2/+2」可靠得多：徒姬超进化给其他随从
                         // +2/+2 时，那些牌的这个值仍然是 0。
                         ev: e.add(0x40).readS32()});
        } else out.push(arr.add(0x20 + i * 8).readS32());
      } catch (e) {}
    }
    return out;
  }
  // 归一化字段名：Unity 自动属性叫 "<HandCardList>k__BackingField"
  function normName(s) {
    return (s || "").toLowerCase().replace(/k__backingfield/g, "")
                    .replace(/[<>_]/g, "");
  }
  // 默认偏移 = 之前一路猜/验证下来的值，按名字找不到时继续用它
  var LAYOUTS = {};
  var ppCall = null, maxppCall = null, ppFail = 0;
  var LAYOUT_RULES = [
    ["maxpp",     /^max(pp|cardpoint|energy|mana|aura)$/],
    ["pp",        /^(pp|cardpoint|energy|mana|aura|ppcount|ppnow|nowpp)$/],
    ["deck",      /^(deck|deckcount|decknum|deckcardcount|deckleft)$/],
    ["life",      /^(life|hp|health|lifecount|leaderlife|nowlife)$/],
    ["turn",      /^(turn|turncount|turntotal|nowturn|turns)$/],
    ["hand",      /(handcardlist|handcards|handlist)$/],
    ["field",     /(fieldcardlist|fieldlist|boardcardlist|boardlist)$/],
    ["played",    /(playedcardlist|playedlist|playcardlist|playedcards)$/],
    ["destroyed", /(destroyedcardlist|destroyedlist|destroycardlist|gravecardlist|cemetery|deadcardlist)$/],
    ["mulligan",  /mulligan/],
    // 进化点 / 超进化点（2026-11-17 试验）：只按名字反射找，**不猜偏移**。
    // 找到了就能知道「这一局双方各进化了几次、什么时候」，再配合场上随从
    // 攻防 +2/+2、+3/+3 的跳变，就能定位到具体是哪张牌进化了。
    // 找不到就是 null，对现有功能零影响。
    // ⚠️ ep / sep 的正则**不能**把 evolvecount 之类收进来：反射是按字段出现
    // 顺序匹配的，谁先命中谁占位，混进来就可能把「进化次数」当成「剩余进化点」。
    ["ep",        /^(ep|epcount|epnum|epnow|evolvepoint|evolvepoints|evolutionpoint)$/],
    ["sep",       /^(sep|sepcount|superep|superevolvepoint|superevolutionpoint)$/],
    // 2026-11-17 实测补上的一组（都在这台客户端的 BattleStatePlayerMpo 上反射到了）：
    //   maxep / maxsep      进化点 / 超进化点的上限 —— 用户确认**两者都是 2**
    //   evolvecount         本局进化总次数（含超进化？待实测确认）
    //   manualevolvecount   手动进化次数
    //   evolveturn          疑似「进化解锁的回合」（用户：进化要过几回合才能用），
    //                       不是「用掉的回合」；等实测日志再定名
    //   superevolveturn     同上，超进化的解锁回合
    //   isevolvedthisturn   这一回合是否已经进化过
    ["maxep",     /^(maxep|maxevolvepoint|maxevolutionpoint)$/],
    ["maxsep",    /^(maxsep|maxsuperevolvepoint|maxsuperevolutionpoint)$/],
    ["evolvecount", /^(evolvecount|evolvecnt|evolutioncount)$/],
    ["manualevolvecount", /^(manualevolvecount|manualevolvecnt)$/],
    ["evolveturn", /^(evolveturn|evolutionturn)$/],
    ["superevolveturn", /^(superevolveturn|superevolutionturn)$/],
    ["isevolvedthisturn", /^(isevolvedthisturn|isevolutionthisturn)$/],
    // 先手 / 后手（2026-11-17）：解锁回合按先后手不同，两个字段对着看就能验出来
    ["first",     /^(isfirstside|isfirst|isfirstplayer)$/],
    // 「这局谁认输」如果能直接读到，就再也不用按血量猜了。
    // 先用精确名字试，找不到就算了（surrender 是 null，不读）。
    ["surrender", /^(issurrender|surrender|isgiveup|giveup|isretire|retire|isconcede|resign|isresign)$/]
  ];
  function resolveLayout(p) {
    var cn = clsName(p) || "?";
    if (LAYOUTS[cn]) return LAYOUTS[cn];
    var L = {pp: 100, maxpp: 100, deck: 32, life: 36, turn: 0x38,
             mulligan: 0x5C, hand: 16, field: 24, played: 112, destroyed: 120,
             names: {}, src: "默认偏移"};
    threadOn();          // 反射要走运行时的元数据接口，做一小会儿马上退出
    if (classGetFields) {
      try {
        var k = objClass(p), depth = 0, seen = {}, got = 0, all = [];
        while (!k.isNull() && depth++ < 6) {
          var it = Memory.alloc(Process.pointerSize), f;
          while (!(f = classGetFields(k, it)).isNull()) {
            var nm = "";
            try { nm = fieldGetName(f).readUtf8String() || ""; } catch (e) {}
            var key = normName(nm);
            if (!key || seen[key]) continue;
            seen[key] = 1;
            // 全部字段名都留着（上限 200）：进化点到底叫什么，光靠猜正则猜不到，
            // 打一局把这份清单打出来就能直接看（2026-11-17 加）。
            if (all.length < 200) all.push(key);
            for (var ri = 0; ri < LAYOUT_RULES.length; ri++) {
              var rk = LAYOUT_RULES[ri][0];
              if (L.names[rk] || !LAYOUT_RULES[ri][1].test(key)) continue;
              try {
                if (fieldGetFlags && (fieldGetFlags(f) & 0x10)) continue;
                var off = fieldGetOffsetFn(f);
                if (off <= 0 || off > 0x400) continue;
                L[rk] = off;
                L.names[rk] = nm + "@0x" + off.toString(16);
                got++;
              } catch (e) {}
            }
          }
          k = classGetParent(k);
        }
        if (got) L.src = "按字段名";
        L.all = all;
      } catch (e) {}
    }
    // 这里原本还会「直接调 C# 的 get_Pp() 属性」。2026-11-07 去掉了：
    // 在别人的 JS 线程上执行托管方法，等于拿游戏的对象跑游戏自己的代码，
    // 既可能撞上主线程正持有的锁，也是必须 attach 线程的理由 —— 而 attach
    // 正是卡死的根源。PP 退回按字段偏移读，够用且绝不碰托管代码。
    threadOff();
    LAYOUTS[cn] = L;
    try { send({layout: cn, src: L.src, names: L.names, all: L.all || []}); } catch (e) {}
    return L;
  }

  // ------------------------------------------------ 卡牌对象诊断（临时） ----
  // 只给 DIAG_CARD 用，平时一行都不执行。
  // 场上卡对象的数组元素本身就是「卡牌对象指针」，0x14/0x1C/0x20 分别是
  // cardId / atk / life（读数组时用的就是这三个），所以这里能直接拿到指针。
  function fieldPtrs(st) {
    var out = [];
    try {
      var L = resolveLayout(st.ptr);
      var arr = st.ptr.add(L.field).readPointer();
      if (arr.isNull()) return out;
      var cnt = arr.add(0x18).readS32();
      if (cnt < 0 || cnt > 60) return out;
      for (var i = 0; i < Math.min(cnt, 12); i++) {
        var e = arr.add(0x20 + i * 8).readPointer();
        if (!e.isNull()) out.push(e);
      }
    } catch (e) {}
    return out;
  }
  function hexOf(p, n) {
    try {
      var ab = p.readByteArray(n);
      if (!ab) return "";
      var u8 = new Uint8Array(ab), s = "";
      for (var i = 0; i < u8.length; i++) {
        s += ("0" + u8[i].toString(16)).slice(-2);
      }
      return s;
    } catch (e) { return ""; }
  }
  function cardClsFields(p) {
    var cn = clsName(p) || "?";
    if (CARD_CLS[cn]) return CARD_CLS[cn];
    var out = [];
    threadOn();
    try {
      var k = objClass(p), depth = 0, seen = {};
      while (!k.isNull() && depth++ < 5) {
        var it = Memory.alloc(Process.pointerSize), f;
        while (!(f = classGetFields(k, it)).isNull()) {
          var nm = "";
          try { nm = fieldGetName(f).readUtf8String() || ""; } catch (e) {}
          var key = normName(nm);
          if (!key || seen[key]) continue;
          seen[key] = 1;
          var off = -1;
          try { off = fieldGetOffsetFn(f); } catch (e) {}
          out.push(nm + "@0x" + (off >= 0 ? off.toString(16) : "?"));
          if (out.length > 160) break;
        }
        k = classGetParent(k);
      }
    } catch (e) {}
    threadOff();
    CARD_CLS[cn] = out;
    return out;
  }
  // 把一侧场上的卡全 dump 出来（含字节 + 类字段清单），供主程序写盘对比
  function dumpSide(side, st) {
    if (!st || !st.ptr) return [];
    var ps = fieldPtrs(st), out = [];
    for (var i = 0; i < ps.length; i++) {
      var e = ps[i];
      try {
        out.push({side: side,
                  cardId: e.add(0x14).readS32(),
                  atk: e.add(0x1C).readS32(),
                  life: e.add(0x20).readS32(),
                  cls: clsName(e) || "?",
                  hex: hexOf(e, 0x60),
                  fields: i === 0 ? cardClsFields(e) : []});
      } catch (e2) {}
    }
    return out;
  }
  function rdInt(p, off, lo, hi, defOff) {
    try {
      var v = p.add(off).readS32();
      if (v >= lo && v <= hi) return v;
    } catch (e) {}
    if (defOff !== off) {
      try {
        var v2 = p.add(defOff).readS32();
        if (v2 >= lo && v2 <= hi) return v2;
      } catch (e) {}
    }
    return null;
  }
  // 「有偏移才读，没有就 null」：给进化点这种**没有硬编码兜底**的字段用。
  // 猜偏移的代价太大（会把别的字段当进化点用），所以只认名字反射到的那个。
  function rdOpt(p, off, lo, hi) {
    if (!off) return null;
    try {
      var v = p.add(off).readS32();
      if (v >= lo && v <= hi) return v;
    } catch (e) {}
    return null;
  }
  function readState(p) {
    try {
      if (!isPlayerState(p)) return null;
      var L = resolveLayout(p);
      // 进化点 / 超进化点：**只认按名字反射到的偏移**（2026-11-17 试验）。
      // 不像 deck/life 那样有硬编码兜底 —— 猜错就是把别的字段当进化点，
      // 宁可读到 null 也不要假的数字。
      var ep = rdOpt(p, L.ep, 0, 9);
      var sep = rdOpt(p, L.sep, 0, 9);
      // 同一族的一组数字（2026-11-17）：上限 / 次数 / 回合。
      // evolvecount 是「本局一共进化了几次」，evolveturn 是「第几回合进化的」。
      var maxep = rdOpt(p, L.maxep, 0, 9);
      var maxsep = rdOpt(p, L.maxsep, 0, 9);
      var evc = rdOpt(p, L.evolvecount, 0, 30);
      var mevc = rdOpt(p, L.manualevolvecount, 0, 30);
      var evt = rdOpt(p, L.evolveturn, -1, 60);
      var sevt = rdOpt(p, L.superevolveturn, -1, 60);
      // 先手/后手是 bool（读 1 字节，不能用 readS32，会把相邻字段一起吃进来）
      var first = null;
      if (L.first) {
        try { first = p.add(L.first).readS8() != 0; } catch (e) {}
      }
      var deck = rdInt(p, L.deck, 0, 45, 32);
      // 生命下限必须放到 0 以下：被斩杀那一帧血量常常是负数（4 - 8 = -4），
      // 旧的下限 0 会把这一帧当成「读到的垃圾值」→ 整个对象判无效 →
      // 于是探针一辈子看不到血量归零，胜负也就永远判不出来（用户 2026-11-06：
      // 打输了没记账，日志里 61 次结束时双方血量都 >0）。
      var life = rdInt(p, L.life, -30, 30, 36);
      var turn = rdInt(p, L.turn, -1, 255, 0x38);
      // 字段合理性校验：对象释放后读到的垃圾值会被挡掉
      if (deck === null || life === null || turn === null) return null;
      var pp = rdInt(p, L.pp, -1, 30, 100);
      var maxpp = rdInt(p, L.maxpp, -1, 30, 100);
      if (pp !== null && maxpp !== null && pp > maxpp) maxpp = pp;
      var hand = readArr(p.add(L.hand).readPointer(), "hand");
      if (hand.length > 10) return null;
      // 手牌“有长度、但每张的卡 id 都是 0” = 读的是**别人的手牌**。
      // 游戏把对手手牌在本地做成占位卡（cardId=0），只有自己的手牌才有真 id。
      // 这条是判断“这份快照是不是我”最硬的证据：比我更硬的没有了。
      // （2026-11-11 用户反馈：内战里我方被认成对手 → 手牌分区消失。）
      var surrender = null;
      if (L.surrender) {
        try { surrender = p.add(L.surrender).readS8() != 0; } catch (e) {}
      }
      var faceDown = false;
      if (hand.length) {
        faceDown = true;
        for (var hi = 0; hi < hand.length; hi++) if (hand[hi]) { faceDown = false; break; }
      }
      return {ptr: p, addr: p.toString(), cls: clsName(p),
              deck: deck, life: life, turn: turn, faceDown: faceDown,
              surrender: surrender,
              ep: ep, sep: sep,
              maxep: maxep, maxsep: maxsep,
              evc: evc, mevc: mevc, evt: evt, sevt: sevt, first: first,
              mulliganDone: p.add(L.mulligan).readS8() != 0,
              pp: pp, maxpp: maxpp,
              hand: hand,
              field: readArr(p.add(L.field).readPointer(), "field"),
              played: readArr(p.add(L.played).readPointer(), "int"),
              destroyed: readArr(p.add(L.destroyed).readPointer(), "int")};
    } catch (e) { return null; }
  }
  // 只对「已经认出来的活对象」调属性：僵尸指针上调方法有崩溃风险，
  // 而每帧最多两次调用足够跟上 PP 的变化。
  function callPp(st) {
    if (!st || ppFail >= 5) return;
    if (ppCall) {
      try {
        var v = ppCall(st.ptr);
        if (v >= 0 && v <= 10) st.pp = v;
        else ppFail++;
      } catch (e) { ppFail++; if (ppFail >= 5) ppCall = null; }
    }
    if (maxppCall) {
      try {
        var mx = maxppCall(st.ptr);
        if (mx >= 0 && mx <= 10 && mx >= (st.pp === null ? 0 : st.pp)) st.maxpp = mx;
      } catch (e) { maxppCall = null; }
    }
  }
  function score(st) {    if (!deckSetN) return 0;
    var s = 0, i;
    var h = st.hand || [], pl = st.played || [], de = st.destroyed || [];
    for (i = 0; i < h.length; i++) if (deckSet["" + h[i]]) s += 3;
    for (i = 0; i < pl.length; i++) if (deckSet["" + pl[i]]) s += 1;
    for (i = 0; i < de.length; i++) if (deckSet["" + de[i]]) s += 1;
    return s;
  }
  // 这一帧是不是「上一局已经结束、新的一局开始了」。
  //
  // 老判据只有「牌库比上一帧多 8 张」。早投降（牌库还剩 30 多张）时新局才 35 张，
  // 差不到 8 —— 于是换局判不出来，上一局的胜负就一直挂着（用户 2026-11-11：
  // 「投降后没加失败数，开新局也没结算上一局」）。
  //
  // 新局的样子其实很好认：**回合数退回 0/1、血量回满、牌库回满**。
  // 反过来「长考」这三样一个都不会变，对手回合也不会让我方 turn 变小。
  function isNewGame(me, lastMe) {
    if (me.deck > lastMe.deck + 8) return true;          // 老判据，留着
    return me.turn + 1 < lastMe.turn                     // 回合数退回（至少小 2）
        && me.life >= lastMe.life                        // 血量回满
        && me.deck >= lastMe.deck;                       // 牌库回满
  }
  function resetMins(me, enemy) {
    meMin = me ? me.life : null;
    enemyMin = enemy ? enemy.life : null;
    overDone = false;              // 新的一局：允许再发结束事件
    overWhy = "";
    overDoneHard = false;
    meAliveSeen = false;
    enemyAliveSeen = false;
    blindSince = 0; blindN = 0; holdN = 0; goneSent = false; hardSince = 0;
  }
  function sendOver(now, why, hard) {
    if (overDone) return;
    overDone = true;
    overWhy = why;
    overDoneHard = !!hard;
    send({over: true, at: now, why: why,
          seq: seqNo(),
          // hard = 候选对象被销毁（不是长考）。主程序看到它就不必等 20 秒门槛。
          hard: !!hard,
          blind_ms: blindSince ? (now - blindSince) : 0,
          me_life: lastMe ? lastMe.life : null,
          enemy_life: lastEnemy ? lastEnemy.life : null,
          me_min: meMin, enemy_min: enemyMin,
          // 牌库数也带上：结算时双方血量都 >0 的话，还能靠「牌库抽空」分胜负
          me_deck: lastMe ? lastMe.deck : null,
          enemy_deck: lastEnemy ? lastEnemy.deck : null,
          me_turn: lastMe ? lastMe.turn : null,
          me_addr: lastMe ? lastMe.addr : null,
          enemy_addr: lastEnemyAddr,
          enemy_cls: lastEnemy ? lastEnemy.cls : null});
  }
  // 读不到「我方」时走这里：补发上一帧顶住。
  //
  // 这条路径**不再发「对局结束」**（2026-11-06 两次踩坑的最终结论）：
  //   - 对手回合长考：游戏十几秒不调用 get_*CardList，池子里的快照全部过期；
  //   - 我方回合长考：玩家在思考，游戏根本不推进状态同步，对象字段读出来是旧的
  //     甚至读不出（日志里 351/359/366 行都是「候选 0」，紧接着我方血量 14、
  //     PP 8/8，明明在对局中）。
  // 这两种情况下「读不到」和「对局结束」毫无关系。8 秒门槛也拦不住 —— 玩家
  // 思考 8 秒太正常了，于是每局被打断十几次，seq 一路 ++，主程序当成十几局，
  // 一局记了 4 次胜利。
  // 现在只补发；真的结束靠两条硬信号：血量归零（dead）、换局（next）。
  function holdOrEnd(now, nCand) {
    if (!lastInfo) {
      // 还没读到过任何一局（程序刚启动 / 已经收尾）：照旧发心跳，
      // 让记牌器知道「现在没在对局里」。
      if (now - lastIdle >= 2000) { lastIdle = now; send({idle: true, at: now}); }
      return;
    }
    if (overDone && !goneSent) {
      // 本局已经结算过（血量归零 / 换局），这时读不到就是真的收尾了：
      // 直接发 idle 让界面回「当前卡组」，并把身份清掉等下一局。
      if (now - lastIdle >= 1200) { lastIdle = now; send({idle: true, at: now}); }
      lastInfo = null;
      blindSince = 0; blindN = 0; holdN = 0;
      lastMe = null; lastEnemy = null; lastEnemyAddr = null;
      meMin = null; enemyMin = null;
      return;
    }
    if (!blindSince) blindSince = now;
    var ms = now - blindSince;
    // 对象真的被销毁了（投降后返回 / 退出到主菜单）：这不是长考，8 秒就够。
    // 只有 nCand === 0 才算 —— 长考时候选还在，走下面 90 秒那条。
    if (nCand === 0) {
      if (!hardSince) hardSince = now;
      // 光「读不到」还不够 —— **新局刚开局、停在换牌那句「保留」时，对象本来
      // 就会整段读不到**。用户 2026-11-11：投降后开新局，8 秒后拿新局开局
      // 的画面（双方 20 血 / 回合 1）当成了「上一局结束」，记了一条 20:20 的负。
      // 所以要求这一局**确实打过**（出过牌，或者已经过了第 1 回合）。
      var playedN = (lastMe && lastMe.played) ? lastMe.played.length : 0;
      var playedEnough = lastMe && (playedN > 0 || (lastMe.turn || 0) >= 2);
      if (!overDone && playedEnough && now - hardSince >= HARD_END_MS) {
        sendOver(now, "gone", true);
        goneSent = true;
        send({note: "候选对象全部消失 " + Math.round((now - hardSince) / 1000)
                    + " 秒，按对局结束处理"});
      }
    } else {
      hardSince = 0;
    }
    // 兜底：读不到超过 90 秒，基本可以认定对局没了（不是长考）。
    // 只发一次；记牌器那边只认「血量归零 / 牌库抽空」的硬证据，不会因此乱记。
    if (ms >= BLIND_END_MS && !overDone) {
      sendOver(now, "gone");
      goneSent = true;
      send({note: "读不到我方 " + Math.round(ms / 1000) + " 秒，按对局结束处理"});
    }
    // gone 之后再补 60 秒（长考回来还能接上，别急着切回卡组预览）。
    if (goneSent && ms >= BLIND_END_MS + BLIND_GRACE_MS) {
      send({idle: true, at: now});
      lastIdle = now;
      lastInfo = null;
      blindSince = 0; blindN = 0; holdN = 0; goneSent = false; hardSince = 0;
      lastMe = null; lastEnemy = null; lastEnemyAddr = null;
      meMin = null; enemyMin = null;
      return;
    }
    if (ms < BLIND_IDLE_MS) {
      var h = {}, kk;
      for (kk in lastInfo) h[kk] = lastInfo[kk];
      h.at = now;
      h.hold = true;
      h.blind_ms = ms;
      h.cands = nCand;
      send(h);
      if (++holdN % 40 === 1)
        send({note: "读不到我方 " + Math.round(ms / 1000) + " 秒，补发上一帧"});
      return;
    }
    // 兜到两分半还读不到：对局大概率是求和 / 掉线 / 退回主菜单了。
    // 只发 idle 切回卡组预览，**不记胜负**（没有归零也没有换局，本来也判不出来）。
    send({idle: true, at: now});
    lastIdle = now;
    lastInfo = null;
    blindSince = 0; blindN = 0; holdN = 0; hardSince = 0;
    lastMe = null; lastEnemy = null; lastEnemyAddr = null;
    meMin = null; enemyMin = null;
  }
  function sample() {
    if (stopped || quitting) return;
    var now = Date.now();
    var costT0 = now;    // 采样耗时（2026-11-17）：怀疑「读进化点会不会卡」时看这个数
    var alive = [], touched = [], i, k, st;
    // 每帧直接读候选对象本身，**不再用「最近有没有被 getter 碰过」当存活判据**。
    // 对手长考 / 动画 / 结算画面里，游戏可能十几秒都不调用任何 get_*CardList，
    // 旧逻辑只要 4 秒没被碰就当成「对象没了」→ 发 {idle:true} → 记牌器把整段
    // 列表销毁重建，用户看到的就是「卡牌全部消失，过一会儿又冒出来」。
    // ts 只保留一个用途：选「我方身份」时用来排除 GC 之后仍可读的僵尸对象。
    for (i = 0; i < cands.length; i++) {
      st = readState(cands[i].p);
      if (st === null) { cands[i].miss = (cands[i].miss || 0) + 1; continue; }
      cands[i].miss = 0;
      st.ts = cands[i].ts;
      alive.push(st);
      if (now - cands[i].ts <= FRESH_MS) touched.push(st);
    }
    // 连续 12 帧（250ms 采样 → 约 3 秒）读不出合法字段 → 对象真的没了，移出池子。
    // 已经认出来的我方 / 对手对象不清：长考期间 getter 可能很久不被调用，
    // 按「被碰过的时间」清池会让读取器在空档期断流（列表清空再重建）。
    var keep = [];
    for (i = 0; i < cands.length; i++) {
      var c = cands[i];
      if ((c.miss || 0) >= 12) continue;
      var known = (lastMe !== null && c.p.equals(lastMe.ptr))
               || (lastEnemyAddr && c.p.toString() === lastEnemyAddr);
      if (!known && now - c.ts > KEEP_MS) continue;
      keep.push(c);
    }
    cands = keep;
    var fresh = alive;

    if (!fresh.length) {
      // 一个候选都读不出来。以前这里 1.5 秒就发「对局结束」，对手长考时
      // 隔几秒就误报一次（用户 2026-11-06 的日志 12 分钟错了 101 次）。
      holdOrEnd(now, 0);
      return;
    }

    // ---- 我方：优先沿用上一次认定的对象（还活着就直接用）。
    var recent = [];
    if (touched.length) {
      var maxTs = 0;
      for (k = 0; k < touched.length; k++)
        if (touched[k].ts > maxTs) maxTs = touched[k].ts;
      for (k = 0; k < touched.length; k++)
        if (maxTs - touched[k].ts <= 1200) recent.push(touched[k]);
    }
    if (!recent.length) {
      if (lastMe !== null) {
        for (k = 0; k < fresh.length; k++)
          if (fresh[k].ptr.equals(lastMe.ptr)) recent.push(fresh[k]);
      }
      if (!recent.length) recent = touched.length ? touched : fresh;
    }

    // ---- 身份判定
    //
    // 2026-11-06 08:4x 日志暴露了真正的问题：**同职业内战时卡组重合分分不开人**。
    //   我方（真身）匹配 26~29 / 对手 匹配 13，两个都远超门槛 3；
    //   而之前是「分数 >=3 里取 ts 最新」，两帧之间两个对象的 ts 只差几十毫秒，
    //   于是身份在两份快照之间来回跳：
    //     332| 我方 0x...bd00（匹配 13，生命 20）Deck=26 Hand=6 Played=8
    //     334| 我方 0x...bb00（匹配 26，生命 20）Deck=27 Hand=4 Played=13
    //   Played 在 8 和 13 之间跳，换局判定看到「Played 少了 5 张」→ 以为新开了一局
    //   → 发 next + 流水号 +1，主程序立刻按血量结算一次。一局里跳了 21 次，
    //   于是「胜利 4 次」。
    //
    // 现在改成**连续性优先**：同一个人身上有几个量是单调的 ——
    //   牌库只会减少（抽牌）、已打出只会增加、血量不会回升。
    // 谁的这三个量和上一帧接得上，谁就是我方；造反的那个直接排除。
    var MIN_SELF = 3;
    function cont(v) {
      if (lastMe === null) return 0;
      if (v.life > lastMe.life + 2) return -1;              // 血量不会自己变多
      if (v.deck > lastMe.deck + 1) return -1;              // 牌库不会变多
      if (v.played.length + 1 < lastMe.played.length) return -1;  // 已打出不会变少
      if (v.deck < lastMe.deck - 6) return -1;              // 一帧抽 6 张不可能
      // 手牌重合（2026-11-11）：对方用和我一样的卡组时，双方牌库/已打出/血量
      // 常常完全一样，按前几项算出来的连续性会打平 —— 打平就只好取“先扫到的”，
      // 于是每帧可能换成另一个人，界面表现为手牌一栏忽有忽无、整窗闪烁。
      // 手牌是这两份快照之间**最不该变**的东西：一回合也就换掉两三张。
      var seen0 = {}, keep0 = 0, i0;
      var vh = v.hand || [], lh = lastMe.hand || [];
      for (i0 = 0; i0 < vh.length; i0++) seen0["" + vh[i0]] = 1;
      for (i0 = 0; i0 < lh.length; i0++) if (seen0["" + lh[i0]]) keep0++;
      // 只按「上一帧的手牌还剩几张在他手上」给 0~18 的惩罚，用**比例**而不是
      // 张数差：我把手牌打空时双方都是 0 重合，就谁也不加分，不会让“手里
      // 有牌的那一方”看起来更像我自己。cont 必须始终 >= 0（下面拿它当
      // 有效性判据），所以这里是加惩罚、不是减奖励。
      // 权重给得比牌库/血量还高：内战里那两项常常一模一样，手牌是唯一还
      // 能分开两个人的东西（一回合最多换掉两三张）。
      var handPen = lh.length ? (1 - keep0 / lh.length) * 18 : 0;
      return Math.abs(v.deck - lastMe.deck) * 2
           + Math.abs(v.played.length - lastMe.played.length)
           + Math.abs(v.life - lastMe.life)
           + handPen;
    }
    // ---- 先按「手牌是不是脸朝下」筛一遍
    //
    // 2026-11-11 用户内战反馈：对方打出【我的前辈·欧丝】之后，记牌器的手牌分区
    // 直接消失。日志里 `me` 在两种快照之间来回跳：
    //   我方 0x...d800（匹配 27，生命 20）→ Deck=29 Life=20 Hand=7
    //   我方 0x...d700（匹配 13，生命 17）→ Deck=31 Life=17 Hand=6   ← 是**对手**
    // 对手那份的 hand 有长度、但 6 个卡 id 全是 0（游戏把对手手牌做成占位卡），
    // 下游把 0 过滤掉就剩空数组，于是「手牌」整段不渲染。
    //
    // 判据因此可以非常硬：**自己的手牌一定有真 id，全 0 的一定不是我。**
    // 之前的 cont()/score() 都是“像不像”，这条是“是不是”。
    var pool = [];
    for (k = 0; k < fresh.length; k++) {
      if (!fresh[k].faceDown) pool.push(fresh[k]);
    }
    if (!pool.length) {
      // 全员脸朝下：这一帧只有「不是我」的对象 —— 结算画面、对手视角、
      // 换牌阶段都会这样。
      //
      // ⚠️ 这里**不能**再「还没认过人就先挑一个」。
      // 2026-11-12 用户实测：赢下那局之后探针收尾把 lastMe 清成 null，
      // 下一帧只有一份 `生命 0 / 牌库 12 / 手牌 3 张脸朝下`，被当成我方，
      // 记牌器据此判「我方生命归零」→ 2 胜 2 负被记成 1 胜 3 负。
      // 脸朝下的对象连血量都不可信，宁可这几帧什么都不显示。
      if (!poolWarn) {
        poolWarn = true;
        send({note: "这一帧只读得到对手（手牌全 0 / 脸朝下），先顶住上一帧"});
      }
      holdOrEnd(now, fresh.length);
      return;
    }
    poolWarn = false;
    var me = null;
    // 先看有没有「明显是新一局」的候选：结算画面会把上一局的对象留着好几秒，
    // 那份快照和上一帧完全接得上（cont=0），按连续性它永远排第一，新一局
    // 反而被挤掉 —— 于是换局判不出来、seq 不 +1，上一局的胜负也就漏记了。
    // 新局的硬特征：牌库比上一帧多出一大截 + 已打出归零 + 血是满的。
    if (lastMe !== null) {
      for (k = 0; k < pool.length; k++) {
        var nv = pool[k];
        if (score(nv) < MIN_SELF) continue;
        if (nv.deck < lastMe.deck + 10) continue;
        if (nv.played.length > 3 || nv.life < 18) continue;
        if (me === null || nv.deck > me.deck) me = nv;
      }
    }
    if (me === null && lastMe !== null) {
      // 有上一帧时按「连续性」挑人，但**必须在卡组重合分最高的那一档里挑**。
      // 只按连续性挑会挑到对手：对手的牌库、已打出同样在单调变化，连续性
      // 往往比我方还漂亮，于是对手被认成我方 —— 我方的牌、我方的衍生物全被
      // 记到「对手已知 / 衍生物」里（用户 2026-11-06 三次反馈的统计串人）。
      // 实测日志：同一帧「我方匹配 12 / 对手匹配 36」连续几十帧，就是这条路径。
      var maxSc1 = 0;
      for (k = 0; k < pool.length; k++) {
        var s1 = score(pool[k]);
        if (s1 > maxSc1) maxSc1 = s1;
      }
      var band1 = Math.max(MIN_SELF, maxSc1 - 3);
      var bestC = 1e9;
      for (k = 0; k < pool.length; k++) {
        var v = pool[k];
        if (score(v) < band1) continue;
        var c = cont(v);
        if (c < 0 || c >= bestC) continue;
        bestC = c; me = v;
      }
      if (me === null) {
        // 同档里都判不了连续性（刚换局 / 对象刚建）：取这一档里最新的
        var mts1 = -1;
        for (k = 0; k < pool.length; k++) {
          if (score(pool[k]) < band1) continue;
          if ((pool[k].ts || 0) > mts1) { mts1 = pool[k].ts || 0; me = pool[k]; }
        }
      }
    }
    if (me === null) {
      // 没有上一帧（开局第一帧 / 刚收尾完），或者候选全都不连续（换局了）：
      // 退到卡组重合分，**只认最高那一档**（差 3 分以内算同档），档内取最新。
      // 这样对手（13 分）顶不掉我方（26 分），我方自己的新旧快照也不会互相打架。
      var maxSc = 0, meTs = -1;
      for (k = 0; k < pool.length; k++) {
        var sc0 = score(pool[k]);
        if (sc0 > maxSc) maxSc = sc0;
      }
      var band = Math.max(MIN_SELF, maxSc - 3);
      for (k = 0; k < pool.length; k++) {
        var v2 = pool[k];
        if (score(v2) < band) continue;
        var vts = v2.ts || 0;
        if (vts > meTs) { meTs = vts; me = v2; }
      }
    }
    // 死亡快照：自己被斩杀那一帧手牌为空，卡组重合分可能掉到 3 分以下，
    // 只能靠「刚才血已经很低 + 手牌空 + 牌库数和刚才对得上」认回来。
    //
    // ⚠️ 2026-11-13 用户实测「双方血量记反」：**对手**被斩杀那一局，
    //    我方手牌恰好也空了（牌都打完了）、牌库数又正好和对手一样（都是 28），
    //    于是对手的尸体被这段救回了「我方」，结束事件写成「我方 0 / 对手 14」，
    //    一局 14 血的胜利被记成负。
    //    所以这段只能用在「本来就是我要死」的场面：上一帧我方的血已经很低，
    //    或者这一帧根本没认到我方（分数不够）。认到了我方就绝不顶替。
    if (lastMe !== null && (me === null || lastMe.life <= 5)) {
      var ref = me || lastMe;
      if (ref.life > 0) {
        for (k = 0; k < fresh.length; k++) {
          var w = fresh[k];
          if (w.life > 0 || (w.hand || []).length) continue;
          if (Math.abs(w.deck - ref.deck) > 2) continue;
          if (me !== null && (w.ts || 0) < (me.ts || 0)) continue;
          me = w;
          break;
        }
      }
    }
    if (me === null) {
      blindN++;
      // 退化模式（只在「确实认不出人」时才允许）：
      //   a) 根本没导入卡组 / 导入的不是这套 —— 分数永远是 0，没有可比的东西；
      //   b) 之前就已经进了退化模式（等有候选重新拿到 >=3 分再退出来）。
      // 一旦认到过我方（lastMe 有值、且是正常模式认出来的），就**不许**再用
      // 退化模式挑人 —— 否则我方对象短暂读不到时会把对手当成我方（旧 bug）。
      var canWeak = !deckSetN || weakMode || (lastMe === null && blindN >= 8);
      if (canWeak) {
        // 按「和上一帧最接得上」挑，挑不到再取最近的。
        if (lastMe !== null) {
          var bestD = 99;
          for (k = 0; k < fresh.length; k++) {
            if (fresh[k].turn + 1 < lastMe.turn) continue;
            var dd = Math.abs(fresh[k].played.length - lastMe.played.length);
            if (dd < bestD) { bestD = dd; me = fresh[k]; }
          }
        }
        if (me === null) me = recent.length ? recent[0] : fresh[0];
        if (me !== null) weakMode = true;
      }
    } else {
      blindN = 0;
      weakMode = false;
      // 认到我方了：盲区结束，gone 的「只发一次」标记复位
      blindSince = 0; goneSent = false; hardSince = 0;
      // 假结束之后数据又回来了（长考超过 90 秒 / 结算画面还留着对象）：
      // 把「本局已结算」解开，否则这一局后面真正打完时探针不再发结束事件，
      // 整局都会漏记。
      // 只解 gone 那一种 —— dead / next 是真有归零证据的结束，结算画面留着
      // 对象是正常现象，解开会让 dead 事件每帧重发（记牌器虽然按流水号挡住了，
      // 日志会刷屏，而且换局残留的旧敌人快照还会被当成新局的胜利）。
      if (overDone && overWhy === "gone" && !overDoneHard
          && me.life > 0 && (lastEnemy === null || lastEnemy.life > 0)) {
        overDone = false;
        send({note: "我方的数据又回来了，撤销「对局结束」锁定（流水号 #" + seqNo() + "）"});
      }
    }
    if (me === null) {
      // 这一帧读不到我方（对手对象不能顶替），补发上一帧顶住，别切走视图。
      holdOrEnd(now, fresh.length);
      return;
    }

    // ---- 对手：跟我方卡组明显不像的那一份里最新的（通常正好是敌人的快照）。
    // 门槛要从「>= MIN_SELF(3)」提到「接近我方分数」：对手打出几张我方卡组里的
    // 中立卡就能拿到 3 分以上，老门槛等于没设 —— 我方的旧快照于是被当成对手，
    // 自己用过的牌和衍生物全进了「对手已知」（用户 2026-11-06 的统计串人）。
    var mySc = score(me) || 0;
    var enemy = null, eTs = -1, eSc = 1e9;
    // 两轮：先只在「手牌脸朝下」的候选里挑 —— 对手的手牌在我们这边读不到内容，
    // 这条同时也是「不是我方」的铁证，比卡组分数可靠（内战里分数分不开人）。
    // 挑不到再退回全部候选（人机 / 读不到对手手牌的场合）。
    for (var ep = 0; ep < 2 && enemy === null; ep++) {
      for (k = 0; k < fresh.length; k++) {
        if (fresh[k].ptr.equals(me.ptr)) continue;
        if (ep === 0 && !fresh[k].faceDown) continue;
        if (score(fresh[k]) >= mySc - 3) continue;   // 跟我方一样像卡组的不是对手
        var t3 = fresh[k].ts || 0;
        if (t3 > eTs) { eTs = t3; enemy = fresh[k]; }
      }
    }
    if (enemy === null) {
      // 镜像对局（对手也在用同一套牌）时分数区分不出来：取分数最低的
      for (k = 0; k < fresh.length; k++) {
        if (fresh[k].ptr.equals(me.ptr)) continue;
        var s3 = score(fresh[k]);
        if (s3 < eSc) { eSc = s3; enemy = fresh[k]; }
      }
    }
    // 还认不到对手：人机 / 掉线时对手的 get_*CardList 可能十几秒都不被调用，
    // 那就到池子里翻一遍最近 20 秒读得通的对象 —— 认不到对手就拿不到对手血量。
    if (enemy === null) {
      for (k = 0; k < cands.length; k++) {
        if (now - cands[k].ts > KEEP_MS) continue;
        if (cands[k].p.equals(me.ptr)) continue;
        var se = readState(cands[k].p);
        if (se === null) continue;
        se.ts = cands[k].ts;
        if (enemy === null || se.ts > enemy.ts) enemy = se;
      }
    }

    // ---- 本局生命最低值 + 换局判定
    // justNew：这一帧是不是「刚换到新的一局」。主程序拿它给上一局的推定结果
    // 定案（改判按钮收起来），不用再靠牌库张数 / 回合数去猜 —— 2026-11-13
    // 用户实测「投降推定胜 → 开新局 → 那条胜被当成『还在同一局』撤销了」。
    var justNew = false;
    if (lastMe === null) {
      // 上一局收尾之后的第一帧：新的一局，流水号 +1（主程序按它去重）
      battleSeq++;
      resetMins(me, enemy);
      justNew = true;
    } else if (me.life > 0 && isNewGame(me, lastMe)) {
      // 换局只认「牌库一下多出一大截」（新局牌库重置，35 张）。
      // **不再**用「已打出张数变少」当换局证据：身份摇摆时 Played 会从 13 掉回 8，
      // 旧逻辑因此每局误判十几次「新的一局」，每误判一次流水号 +1、主程序记一次
      // 胜负（2026-11-06 一局记了 4 次胜利就是这么来的）。
      if (!overDone) sendOver(now, "next");
      battleSeq++;
      blindSince = 0; blindN = 0; holdN = 0;
      resetMins(me, enemy);
      justNew = true;
    } else {
      meMin = Math.min(meMin === null ? me.life : meMin, me.life);
      if (enemy) enemyMin = Math.min(enemyMin === null ? enemy.life : enemyMin, enemy.life);
    }
    if (me.life > 0) meAliveSeen = true;
    if (enemy !== null && enemy.life > 0) enemyAliveSeen = true;

    // 对手这一帧没认到：沿用上一次那条，别把「对手」整段清空
    // （一清空就等于把对手已知牌、对手血量全丢掉，界面像被重置了）。
    var enemyOut = enemy || lastEnemy;

    // PP 用属性调用取一次（字段偏移猜错时这是唯一准的来源）
    callPp(me);
    callPp(enemyOut);

    // 牌库只会减少（洗回牌库除外）。同一局里突然多出 6 张以上、回合数又没
    // 退回第 1 回合 —— 这一帧八成把我方认成了对面（用户 2026-11-06：牌库数
    // 偶尔显示成对手的）。这种情况沿用上一帧的数字：宁可晚一帧更新，也不能
    // 把对手的牌库写到界面上。
    var deckOut = me.deck;
    if (lastMe !== null && deckOut > lastMe.deck + 6 && me.turn >= lastMe.turn)
      deckOut = lastMe.deck;

    var info = {
      addr: me.addr, cls: me.cls, cands: fresh.length, msc: score(me),
      deck: deckOut, life: me.life, mulliganDone: me.mulliganDone,
      // 这份快照的手牌是不是脸朝下（读不到内容）。正常永远是 false；
      // 万一是 true，主程序会沿用上一帧的手牌，别让手牌分区整段消失。
      faceDown: !!me.faceDown,
      surrender: me.surrender === true,
      turn: me.turn, pp: me.pp, maxpp: me.maxpp,
      // 进化点 / 超进化点（试验）：读不到就是 null。主程序只做日志，
      // 不参与任何判定，确认能读到之后才接「EP 变化 + 攻防跳变」的组合判断。
      ep: me.ep, sep: me.sep,
      maxep: me.maxep, maxsep: me.maxsep,
      evc: me.evc, mevc: me.mevc, evt: me.evt, sevt: me.sevt,
      first: me.first,
      hand: me.hand, field: me.field, played: me.played, destroyed: me.destroyed,
      // 刚换到新的一局（主程序用它给上一局的「推定」定案，见 _same_battle）
      newGame: justNew,
      seq: seqNo(),
      enemy: enemyOut ? {addr: enemyOut.addr, cls: enemyOut.cls, life: enemyOut.life,
                      deck: enemyOut.deck, esc: score(enemyOut), turn: enemyOut.turn,
                      pp: enemyOut.pp, maxpp: enemyOut.maxpp,
                      ep: enemyOut.ep, sep: enemyOut.sep,
                      maxep: enemyOut.maxep, maxsep: enemyOut.maxsep,
                      evc: enemyOut.evc, mevc: enemyOut.mevc,
                      evt: enemyOut.evt, sevt: enemyOut.sevt,
                      // 1.1 记牌器「对手已知」用：打出的牌 + 战场 + 墓地。
                      // 只出现在战场/墓地、没进过 played 的就是衍生物 token。
                      // 战场元素带上 atk/life（2026-11-11）：判断这张随从
                      // 有没有进化过 —— 进化 +2/+2，属性会超过卡表的基础值，
                      // 「进化时 / 超进化时」那类加手牌效果要靠它才敢记。
                      field: (enemyOut.field || []).map(function (m) {
                        return (m && m.cardId)
                          ? {cardId: m.cardId, uid: m.uid, atk: m.atk,
                             life: m.life, ev: m.ev} : 0; }),
                      played: enemyOut.played, destroyed: enemyOut.destroyed,
                      stale: !enemy,
                      hand_n: (enemyOut.hand || []).length} : null,
      at: now
    };
    lastMe = me;
    // 对手对象偶尔会有一两帧取不到（正在换对象），别把它清空，
    // 否则结束事件里就没有对手血量，赢局会被判成「无法判定」。
    if (enemy) { lastEnemy = enemy; lastEnemyAddr = enemy.addr; }
    lastInfo = info;
    holdN = 0;
    // ---- 卡牌诊断 dump（临时）：每秒一次，把双方场上卡对象的字节写出去 ----
    // 只在 _diag_card.flag 存在时执行；带上一句当前的点数/回合，方便和
    // 「谁进化了」对齐时间。
    if (DIAG_CARD && now - cardDumpAt > 1000) {
      cardDumpAt = now;
      try {
        var _cards = dumpSide("我方", me).concat(dumpSide("对手", enemyOut));
        if (_cards.length) {
          send({carddump: _cards, at: now, turn: me.turn,
                ep: me.ep, sep: me.sep, evc: me.evc, mevc: me.mevc,
                eep: (enemyOut || {}).ep, esep: (enemyOut || {}).sep,
                eevc: (enemyOut || {}).evc});
        }
      } catch (e) {}
    }
    send(info);

    // ---- 血量归零：当帧直接判定结束。
    // 这才是最可靠的信号 —— 以前只等「对象消失」才发结束事件，可游戏结算画面
    // 还会留着对象好几秒，等到消失时血量早就不是 0 了，于是永远判不出胜负。
    if ((me.life <= 0 && meAliveSeen)
        || (enemyOut !== null && enemyOut.life <= 0 && enemyAliveSeen)) {
      sendOver(now, "dead");
    }
    // ---- 采样耗时自检（2026-11-17）：只在「这一帧明显慢」时吱一声。
    // 正常应该在 1~3ms（纯内存读）；如果动辄几十毫秒，那才是真卡顿源，
    // 到时候重点查的是日志写盘，不是 ep/sep 的几次 readS32。
    try {
      var cost = Date.now() - costT0;
      if (cost >= 12) {
        var lastSlow = write_state.slowAt || 0;
        if (now - lastSlow > 5000) {          // 5 秒最多一条，别自己刷屏
          write_state.slowAt = now;
          send({note: "采样耗时 " + cost + "ms（候选 " + alive.length
                     + "，参考值：正常 1~3ms）"});
        }
      }
    } catch (e) {}
  }
  var hooked = {};
  for (var gi = 0; gi < getters.length; gi++) {
    var gaddr = getters[gi], gkey = gaddr.toString();
    if (hooked[gkey]) continue;
    hooked[gkey] = 1;
    try {
      Interceptor.attach(gaddr, {onEnter: function(args) { noteCand(args[0]); }});
    } catch (e) {}
  }
  // 初始化全部做完：把 JS 线程从 IL2CPP 运行时摘掉，之后只做裸内存读。
  // （这是一切的根子，见上面 threadOn/threadOff 的注释。）
  threadOff();
  if (deckFn === null) send({err: "no deck getter"});
  // 250ms 采一次：游戏的状态对象是「每次同步新建一份」，结算那一帧存活很短，
  // 500ms 采样经常整帧错过（日志里 10 局一次都没读到 0 血，就是这个原因）。
  var timer = setInterval(sample, 250);
  send({hooked: true});

  // ------------------------------------------------------------ 退出保护 ---
  // 「开着助手时关游戏 → 游戏卡死 / 崩溃」的根因之一就在这段。
  //
  // 关键是**别在退出回调里拆 hook**：gum 的 detach 要挂起全部线程再还原代码，
  // 而我们正站在 ExitProcess 的 hook 里（当前线程就在 trampoline 上），
  // 自己等自己 → 进程卡死在退出流程里，表现就是窗口「未响应」。
  // 上一版写成 onEnter 里直接 Interceptor.detachAll()，反而加剧了卡死。
  //
  // 现在的做法：这里只做「停采样 + 报信」，真正的 detach 交给 Python 侧
  // （不在游戏线程上），JS 侧一帧活都不干了，退出流程照常走。
  try {
    function flagQuit(why) {
      if (quitting) return;
      quitting = true;
      try { clearInterval(timer); } catch (e) {}
      try { send({quitting: true, why: why}); } catch (e) {}
    }
    var exitAddr = (Module.getExportByName
                    ? Module.getExportByName("kernel32.dll", "ExitProcess")
                    : Module.findExportByName(null, "ExitProcess"));
    if (exitAddr) {
      Interceptor.attach(exitAddr, {
        onEnter: function () { flagQuit("ExitProcess"); }
      });
    }
    // 有些版本直接 TerminateProcess(-1, 0) 走人；只认「杀自己」的那一种，
    // 免得游戏顺手结束子进程时被误判成退出（那会让读取器提前收工）。
    var termAddr = (Module.getExportByName
                    ? Module.getExportByName("kernel32.dll", "TerminateProcess")
                    : Module.findExportByName(null, "TerminateProcess"));
    if (termAddr) {
      Interceptor.attach(termAddr, {
        onEnter: function (args) {
          try {
            if (args[0].toInt32() === -1) flagQuit("TerminateProcess");
          } catch (e) {}
        }
      });
    }
  } catch (e) {}
  rpc.exports = {
    // 卡组换了（换卡组 / 重新导入）时主程序会调用它。就算调不到也不影响，
    // 因为创建脚本时已经把卡组嵌进来了。
    setDeck: function(ids) { return setDeck(ids); },
    set_deck: function(ids) { return setDeck(ids); },
    stop: function() {
      stopped = true;
      try { clearInterval(timer); } catch (e) {}
      try { Interceptor.detachAll(); } catch (e) {}
      lastMe = null; lastEnemy = null; cands = [];
      lastInfo = null; blindSince = 0; blindN = 0;
      meMin = null; enemyMin = null;
      return true;
    }
  };
})();
"""


def _parse_args(argv):
    """argv[1] = 目标 PID（缺省自动找游戏进程），argv[2] = 最长运行秒数。"""
    pid = 0
    wait = DEFAULT_WAIT
    if len(argv) > 1:
        try:
            pid = int(argv[1])
        except (TypeError, ValueError):
            pid = 0
    if len(argv) > 2:
        try:
            wait = int(argv[2])
        except (TypeError, ValueError):
            wait = DEFAULT_WAIT
    return pid, wait


def _track_draw(st, p):
    """更新爆牌/洗回统计（就地对 st 修改，并写回 p 的 overdraw/returned）。

    - 爆牌：手牌满(8)时抽牌，牌库减少但手牌没增加 → 差额计入 over。
    - 洗回：牌库数量变多 → ret=True。
    - 换牌阶段(mulliganDone=False)重置本局统计。
    """
    try:
        deck = int(p.get("deck") or 0)
        hand_n = len(p.get("hand") or [])
        mull = bool(p.get("mulliganDone"))
        if not mull:
            st.update(deck=deck, hand=hand_n, mull=False, over=0, ret=False)
        else:
            if st["mull"] and st["deck"] is not None:
                dd = st["deck"] - deck
                dh = hand_n - st["hand"]
                if dd > 0 and st["hand"] >= 8 and dh < dd:
                    st["over"] += dd - dh
                    print("爆牌 +%d（手牌 %d）" % (dd - dh, hand_n), flush=True)
                if deck > st["deck"]:
                    st["ret"] = True
            st.update(deck=deck, hand=hand_n, mull=True)
        p["overdraw"] = st["over"]
        p["returned"] = st["ret"]
        # 爆掉的卡 id 列表（由牌库列表 diff 提供；当前探测未确定偏移，先给空）
        p["overdraw_cards"] = st.get("over_cards") or []
    except Exception:
        pass


def _stop_requested():
    """控制台是否请求停止（写 logs/_frida.stop）。"""
    try:
        return os.path.exists(STOP_FILE)
    except Exception:
        return False


def _pid_alive(pid):
    """游戏进程还活着吗（Windows）。

    ⚠️ OpenProcess 失败不等于进程没了：游戏以管理员身份跑、或进程正在退出
       时，QUERY_LIMITED_INFORMATION 会返回 ACCESS_DENIED(5)。老写法直接当成
       「已退出」→ 读取器提前收工 → 控制台马上又挂上去，来回折腾正好卡在
       退出流程里。现在只有 ERROR_INVALID_PARAMETER(87) / 找不到进程才算死。
    """
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))
        if not h:
            err = ctypes.get_last_error() if hasattr(ctypes, "get_last_error") else 0
            if err in (0, 5):          # 0 = 拿不到错误码；5 = 拒绝访问
                return True            # 当作还活着，交给别的判据
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259   # STILL_ACTIVE
    except Exception:
        return True


def _pid_age(pid):
    """进程年龄（秒），拿不到返回 None。见 shadow_warchess_reader.pid_age。"""
    try:
        import shadow_warchess_reader as RW
        return RW.pid_age(pid)
    except Exception:
        return None



def clear_stop():
    """清掉停止标记（启动时先清，退出时再清）。"""
    try:
        os.remove(STOP_FILE)
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _load_deck_ids():
    """读当前卡组（导入的）的卡 id 列表，给 JS 做「谁是我方」的身份判定。"""
    try:
        import deck_reader
        ids = list(deck_reader._CACHE.get("deck_ids") or [])
        if ids:
            return [int(x) for x in ids]
        decks = deck_reader._decks_from_file()
        if decks:
            active = deck_reader._active_name()
            item = next((d for d in decks if d.get("name") == active), decks[0])
            return [int(x) for x in (item.get("cards") or [])]
    except Exception:
        pass
    return []


def _write_status(why, pid=0, note=""):
    """把「实时读取为什么没起来」写进 logs/reader_status.json。

    2026-11-13：朋友的机器上界面只有一句「读取未启动」，看不出是**没找到游戏
    进程**还是**根本没启动读取**，来回猜了好几轮。这里落一行小文件，记牌器
    直接把它显示在状态栏上，谁的问题一眼就知道。
    """
    try:
        path = os.path.join(HERE, "logs", "reader_status.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"why": why, "pid": int(pid or 0), "note": note,
                       "at": int(time.time() * 1000)}, fh,
                      ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def main():
    pid, wait = _parse_args(sys.argv)
    image = ""
    if pid <= 0:
        # 不传 PID 时自动找游戏进程（以前默认写死 27692，换局就连错对象）
        try:
            import shadow_warchess_reader as RW
            pid, image = RW.find_game_pid()
        except Exception as e:
            pid, image = 0, None
            _write_status("error", 0, str(e))
        if not pid:
            _write_status("nopid", 0, "没找到游戏进程")
    if not pid:
        print("没找到游戏进程，实时读取未启动", flush=True)
        return 1

    # 刚出生 / 正在收尾的进程不碰。
    # 关掉游戏后模拟器会立刻用新 pid 重新起来，控制台一看「换了 pid」就当成
    # 玩家重开游戏、马上挂上去 —— 那个进程还在初始化，一注入就卡住不动。
    # 这里原地等它长大（最多 15 秒），不退出，免得控制台反复拉起新进程。
    MIN_AGE = 15.0
    age = _pid_age(pid)
    if age is not None and age < MIN_AGE:
        left = MIN_AGE - age
        print("进程 %s 才启动 %.1f 秒，等 %.0f 秒再挂" % (pid, age, left),
              flush=True)
        while left > 0:
            time.sleep(min(1.0, left))
            left -= 1.0
            if not _pid_alive(pid):
                print("等待期间游戏已退出，实时读取未启动", flush=True)
                _write_status("nopid", 0, "等待期间游戏已退出")
                return 1

    # 单实例：app.py 和 run_companion.py 都可能拉起它，双开会挂两个 agent
    try:
        import singleton
        if not singleton.acquire("SvwbFrida"):
            print("已有实时读取在跑，跳过本次启动", flush=True)
            return 0
    except Exception:
        pass

    clear_stop()
    print("attach", pid, "wait", wait, flush=True)
    session = None
    script = None
    try:
        session = frida.attach(pid)
        _write_status("ok", pid, image)
        # 卡组 id 在创建脚本时就嵌进去：身份判定完全依赖它，一旦拿不到，
        # 「我方」和「对手」会每帧互换（手牌乱跳、胜负判不出）。
        # 以前靠 RPC 下推，在游戏进程里报过 unable to find method 'setDeck'。
        deck_ids = _load_deck_ids()
        diag_flag = os.path.join(HERE, "logs", "_diag_fields.flag")
        diag_on = os.path.exists(diag_flag)
        src = JS.replace("= __DECK_IDS__;",
                         "= " + json.dumps(deck_ids) + ";")
        src = src.replace("__DIAG_FIELDS__", "true" if diag_on else "false")
        if diag_on:
            print("诊断模式：启动后 dump 对战相关类字段到 logs/_fields.txt", flush=True)
        # 卡牌对象诊断（找进化标志位用，跑一局就够）
        card_diag = os.path.exists(os.path.join(HERE, "logs", "_diag_card.flag"))
        src = src.replace("__DIAG_CARD__", "true" if card_diag else "false")
        if card_diag:
            print("卡牌诊断：每秒 dump 场上卡对象到 logs/_cards.txt"
                  "（删掉 logs/_diag_card.flag 可关闭）", flush=True)
        print("内置卡组 %d 个 id（身份判定用）" % len(deck_ids), flush=True)
        script = session.create_script(src)
        latest = {}
        write_state = {"key": None, "heartbeat": 0.0, "line": None}
        dump_card = {"fh": None, "cls": set()}   # 卡牌诊断：logs/_cards.txt

        callback_enabled = {"value": True}
        # 游戏开始退出时 JS 会报信，这里收到就立刻收工（见下面的退出保护）
        quit_ev = threading.Event()
        # GameAssembly.dll 还没加载：这次别挂，退出后让控制台过一会儿重试
        nomod = {"value": False}
        # 爆牌 / 洗回统计状态
        st = {"deck": None, "hand": 0, "mull": False,
              "over": 0, "ret": False, "over_cards": []}
        dump_fh = {"fh": None}

        def on_dump(p):
            """把 dump 出来的类字段写 logs/_fields.txt（诊断用，跑一次即可）。"""
            try:
                if dump_fh["fh"] is None:
                    dump_fh["fh"] = open(os.path.join(HERE, "logs", "_fields.txt"),
                                         "w", encoding="utf-8")
                fh = dump_fh["fh"]
                fh.write("=== %s\n" % p.get("dump"))
                for line in p.get("fields") or []:
                    fh.write("    %s\n" % line)
                fh.write("\n")
                fh.flush()
            except Exception:
                pass

        def on_message(m, d):
            if not callback_enabled["value"]:
                return
            if m.get("type") == "error":
                # 以前这里直接 return，注入脚本抛异常时日志里一片空白，
                # 只能看到「hook 到了但读不到东西」，完全没法定位。
                print("SCRIPT ERROR:", m.get("description") or m,
                      m.get("stack") or "", flush=True)
                return
            if m.get("type") != "send":
                return
            p = m.get("payload") or {}
            if p.get("noModule"):
                print("游戏还没加载 GameAssembly.dll（%s），这次先不挂"
                      % (p.get("err") or "?"), flush=True)
                _write_status("nomod", pid, "GameAssembly.dll 还没加载")
                nomod["value"] = True
                return
            if p.get("quitting"):
                # 游戏开始退出了（ExitProcess / TerminateProcess 被调用）。
                # JS 那边已经停掉采样，这里只负责尽快把自己摘干净：
                # 一秒内就从游戏进程里 detach，别留在退出流程里搅局。
                print("游戏开始退出（%s），立即卸载 hook" % (p.get("why") or "?"),
                      flush=True)
                quit_ev.set()
                return
            if p.get("dump"):
                on_dump(p)
                return
            if "dump_done" in p:
                try:
                    if dump_fh["fh"] is not None:
                        dump_fh["fh"].close()
                        dump_fh["fh"] = None
                except Exception:
                    pass
                print("字段 dump 完成：%s 个类（logs/_fields.txt）" % p.get("dump_done"),
                      flush=True)
                try:
                    os.remove(os.path.join(HERE, "logs", "_diag_fields.flag"))
                except Exception:
                    pass
                return
            if p.get("err") or p.get("hooked"):
                print("MSG:", m)
                return
            if p.get("note"):
                # 探针自己的一句话（盲区补发之类），限频在 JS 侧做
                print("探针：%s" % p.get("note"), flush=True)
                return
            if p.get("layout"):
                # 字段名反射的结果：一眼能看出 PP / 已打出 / 墓地是按名字找到的，
                # 还是退回了硬编码偏移。以后偏移对不上时先看这一行。
                nm = p.get("names") or {}
                print("字段布局（%s）：%s" % (
                    p.get("src") or "?",
                    "  ".join("%s=%s" % (k, v) for k, v in nm.items())
                    or "没匹配到任何字段名，全部沿用默认偏移"), flush=True)
                # 2026-11-17：进化点试验 —— 把反射到的**全部字段名**也打出来
                # （一次会话一个职业只打一次）。里面有没有 evolve/point 之类的
                # 字段，决定了「直接读进化点」这条路走不走得通。
                allf = p.get("all") or []
                if allf:
                    print("  全部字段（%d）：%s" % (len(allf), " ".join(allf)),
                          flush=True)
                    hot = [x for x in allf if ("evol" in x or "super" in x
                                               or "point" in x or x in ("ep", "sep"))]
                    print("  进化相关：%s" % (" ".join(hot) if hot else "（没有）"),
                          flush=True)
                return
            if p.get("carddump"):
                # 卡牌对象诊断：每秒一次，落 logs/_cards.txt。
                # 一行一张卡：cardId / 攻防 / 类名 / 0x00~0x60 原始字节。
                # 用法：同一张卡「进化前」「进化后」的两行一比，变的那几个字节
                # 里就有进化标志；再和「被徒姬 buff 但没进化」的卡对比，就能排除
                # 「只是加身材」的字节。
                try:
                    if dump_card["fh"] is None:
                        dump_card["fh"] = open(
                            os.path.join(HERE, "logs", "_cards.txt"), "w",
                            encoding="utf-8")
                    fh = dump_card["fh"]
                    fh.write("# ==== 回合=%s 我方 ep=%s sep=%s evc=%s(手动%s)"
                             " | 对手 ep=%s sep=%s evc=%s\n"
                             % (p.get("turn"), p.get("ep"), p.get("sep"),
                                p.get("evc"), p.get("mevc"), p.get("eep"),
                                p.get("esep"), p.get("eevc")))
                    for c in p.get("carddump") or []:
                        fh.write("%-3s cardId=%-10s atk=%-3s life=%-3s cls=%-24s %s\n"
                                 % (c.get("side"), c.get("cardId"),
                                    c.get("atk"), c.get("life"),
                                    c.get("cls"), c.get("hex") or ""))
                        fl = c.get("fields") or []
                        if fl and c.get("cls") not in dump_card["cls"]:
                            dump_card["cls"].add(c.get("cls"))
                            fh.write("    类 %s 字段：%s\n"
                                     % (c.get("cls"), "  ".join(fl)))
                    fh.flush()
                except Exception:
                    pass
                return
            if p.get("over"):
                # 对局结束事件：把「双方最后血量」落盘，胜负由 deck_tracker 统一记账。
                # 以前靠「我方生命 / 对手生命到 0」在 500ms 轮询里抓，换局后对象一换
                # 就再也抓不到，于是第二把赢了也不加胜场。
                rec = {"at": int(p.get("at") or 0),
                       "why": p.get("why"),
                       "seq": p.get("seq"),
                       # 漏了这一行，回到消费端 blind_ms 只有 8 秒、过不了
                       # 「盲区 ≥ 20 秒」那道门槛 → 投降那局被判成掉帧，一条都没记。
                       "hard": bool(p.get("hard")),
                       "blind_ms": p.get("blind_ms"),
                       "me_life": p.get("me_life"),
                       "enemy_life": p.get("enemy_life"),
                       "me_min": p.get("me_min"),
                       "enemy_min": p.get("enemy_min"),
                       "me_deck": p.get("me_deck"),
                       "enemy_deck": p.get("enemy_deck"),
                       "me_turn": p.get("me_turn"),
                       "me_addr": p.get("me_addr"),
                       "enemy_cls": p.get("enemy_cls"),
                       "written": time.time()}
                tmp = END_PATH + ".tmp"
                try:
                    with open(tmp, "w", encoding="utf-8") as fh:
                        json.dump(rec, fh, ensure_ascii=False)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, END_PATH)
                except Exception as e:
                    print("battle_end write err", e, flush=True)
                print("对局结束：我方生命=%s 对手生命=%s（%s）"
                      % (rec["me_life"], rec["enemy_life"], rec["why"]), flush=True)
                write_state["key"] = None      # 下一局强制重写快照
                write_state["line"] = None
                return
            if p.get("idle"):
                # 空闲心跳证明 agent 正常，避免 watchdog 误判读取器失联。
                if time.monotonic() - write_state["heartbeat"] >= 2.0:
                    tmp = OUT + ".tmp"
                    try:
                        with open(tmp, "w", encoding="utf-8") as fh:
                            json.dump({"idle": True, "at": p.get("at")}, fh)
                        os.replace(tmp, OUT)
                        write_state["heartbeat"] = time.monotonic()
                        write_state["key"] = "idle"
                    except Exception as e:
                        print("write err", e, flush=True)
                return
            # 爆牌 / 洗回统计
            _track_draw(st, p)
            latest.clear()
            latest.update(p)
            # 内容无变化时只每 2 秒刷新心跳；有变化立即原子写快照。
            state_for_key = dict(p)
            # at / blind_ms 每帧都在变，不能参与「内容有没有变」的比较，
            # 否则补发帧会 4 次/秒地重写文件。
            state_for_key.pop("at", None)
            state_for_key.pop("blind_ms", None)
            state_key = json.dumps(state_for_key, sort_keys=True, ensure_ascii=False)
            now_mono = time.monotonic()
            if state_key != write_state["key"] or now_mono - write_state["heartbeat"] >= 2.0:
                tmp = OUT + ".tmp"
                try:
                    with open(tmp, "w", encoding="utf-8") as fh:
                        json.dump(latest, fh, ensure_ascii=False)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp, OUT)
                    write_state["key"] = state_key
                    write_state["heartbeat"] = now_mono
                except Exception as e:
                    print("write err", e, flush=True)
            # 日志限频：addr 每帧都变（游戏每次同步新建一份状态对象），
            # 不能拿它当「有没有变化」的判据，否则 4 次/秒刷屏。
            objs = "%s|%s|%s|%s|%s|%s|%s" % (
                p.get("cls"), p.get("cands"), p.get("msc"), p.get("life"),
                (p.get("enemy") or {}).get("cls"),
                (p.get("enemy") or {}).get("esc"),
                (p.get("enemy") or {}).get("life"))
            if objs != write_state.get("objs"):
                write_state["objs"] = objs
                _en = p.get("enemy") or {}
                print("读取对象：我方 %s %s（卡组匹配 %s，生命 %s，手牌 %s 张%s）/ 对手 %s（匹配 %s，生命 %s）/ 候选 %s"
                      % (p.get("cls"), p.get("addr"), p.get("msc"), p.get("life"),
                         len(p.get("hand") or []),
                         ("，脸朝下" if p.get("faceDown") else "")
                         + ("，已认输" if p.get("surrender") else ""),
                         _en.get("cls") or "（未识别）", _en.get("esc"),
                         _en.get("life"), p.get("cands")), flush=True)
            seq = p.get("seq")
            if seq is not None and seq != write_state.get("seq"):
                write_state["seq"] = seq
                print("本局流水号：%s" % seq, flush=True)
            line = "Deck=%s Life=%s Hand=%s Played=%s 能量=%s/%s 回合=%s 候选=%s 对手=%s" % (
                p.get("deck"), p.get("life"), len(p.get("hand") or []),
                len(p.get("played") or []), p.get("pp"), p.get("maxpp"),
                p.get("turn"), p.get("cands"),
                (p.get("enemy") or {}).get("life"))
            # 进化点：只有真读到（不是 None）才写进这一行。这一行本来就按「内容
            # 有变化才打」，所以进化点一变就会留下一条日志，正好用来验证反射
            # 有没有找到字段（读不到就完全不影响原有日志）。
            _ep, _sep = p.get("ep"), p.get("sep")
            if _ep is not None or _sep is not None:
                _en_ep = (p.get("enemy") or {}).get("ep")
                _en_sep = (p.get("enemy") or {}).get("sep")
                line += " 进化点=%s/%s 超进化点=%s/%s" % (
                    "-" if _ep is None else _ep,
                    "-" if _en_ep is None else _en_ep,
                    "-" if _sep is None else _sep,
                    "-" if _en_sep is None else _en_sep)
                # 同一族的次数 / 回合 / 上限：全为 None 时整段不打，保持日志干净
                _mx, _mxsep = p.get("maxep"), p.get("maxsep")
                _evc, _mevc = p.get("evc"), p.get("mevc")
                _evt, _sevt = p.get("evt"), p.get("sevt")
                _bits = []
                if _mx is not None:
                    _bits.append("上限=%s/%s" % (_mx, _mxsep))
                if _evc is not None:
                    # 我方/对手的进化总次数并排：对手手动进化时点数会掉，
                    # 但对手用效果自动进化时点数不动 —— 只有这个数能看出来。
                    _en_evc = (p.get("enemy") or {}).get("evc")
                    _bits.append("进化次数=%s(手动%s)/敌%s"
                                 % (_evc, _mevc,
                                    "-" if _en_evc is None else _en_evc))
                if _evt is not None:
                    _bits.append("进化回合=%s" % _evt)
                if _sevt is not None:
                    _bits.append("超进化回合=%s" % _sevt)
                if p.get("first") is not None:
                    _bits.append("先手" if p.get("first") else "后手")
                if _bits:
                    line += " " + " ".join(_bits)
            _en2 = p.get("enemy") or {}
            if _en2:
                line += "（对手打出=%s 场=%s 墓=%s%s）" % (
                    len(_en2.get("played") or []),
                    len(_en2.get("field") or []),
                    len(_en2.get("destroyed") or []),
                    "，上一帧数据" if _en2.get("stale") else "")
            if line != write_state["line"]:
                print("state: " + line, flush=True)
                write_state["line"] = line

        script.on("message", on_message)
        script.load()
        # 卡组在 create_script 时已经嵌进去了；这里只是在「换了卡组」时补推一次。
        # RPC 调不到也不影响任何功能，所以失败只记一次、不刷屏。
        last_push = 0.0
        pushed = {"sig": None, "warned": False, "busy": False}

        def push_deck(force=False):
            """把当前卡组推给 JS（身份判定用）。

            ⚠️ RPC 是**同步等游戏回包**的，没有超时。游戏一旦卡住（正在退出、
               或者真的假死），这个调用会把主循环一起钉死 —— 之后连
               「收到了停止请求」都读不到，控制台就以为读取器失联。
               所以放进短命线程里等 2.5 秒，等不到就放弃这一轮。
            """
            nonlocal last_push
            now_t = time.time()
            if not force and now_t - last_push < 120:
                return
            if pushed["busy"]:
                return
            last_push = now_t
            pushed["busy"] = True

            def _call():
                try:
                    ids = _load_deck_ids()
                    got = script.exports_sync.set_deck(ids)
                except Exception as e:
                    if not pushed["warned"]:
                        pushed["warned"] = True
                        print("热更新卡组不可用（%s）；已内嵌的卡组照常生效" % e,
                              flush=True)
                    return
                finally:
                    pushed["busy"] = False
                sig = (got, tuple(sorted(ids)))
                if sig == pushed["sig"]:
                    return
                pushed["sig"] = sig
                if got:
                    print("已热更新卡组 %d 个 id 用于身份判定" % got, flush=True)

            th = threading.Thread(target=_call, daemon=True)
            th.start()
            th.join(2.5)

        push_deck(force=True)
        t0 = time.time()
        while time.time() - t0 < wait:
            push_deck()
            if nomod["value"]:
                break
            if quit_ev.is_set():
                break
            if _stop_requested():
                print("收到停止请求，卸载 hook", flush=True)
                break
            # 控制台退出后自己收尸，别留孤儿 Frida 进程
            try:
                import parent_guard
                if parent_guard.orphaned():
                    print("控制台已退出，停止实时读取", flush=True)
                    break
            except Exception:
                pass
            try:
                if not _pid_alive(pid):
                    print("游戏进程已退出，停止实时读取", flush=True)
                    break
            except Exception:
                pass
            time.sleep(1)
    except Exception as e:
        print("实时读取失败：%s" % e, flush=True)
        _write_status("error", pid, str(e))
        return 1
    finally:
        # 先屏蔽消息回调；目标进程已退出时不再向它发 unload/detach 请求。
        try:
            if "callback_enabled" in locals():
                callback_enabled["value"] = False
        except Exception:
            pass
        # 已经收到退出信号：先让游戏自己走完。
        # 进程要是 1.5 秒内就没了，那是最理想的结果 —— 什么都不用做，
        # agent 跟着进程一起消失，我们不去碰它（退出过程中 detach 反而危险）。
        # 还活着，说明它不是「退出」而是「回到启动器」或者已经卡住，那才摘连接。
        if "quit_ev" in locals() and quit_ev.is_set():
            t_end = time.time() + 1.5
            while time.time() < t_end and _pid_alive(pid):
                time.sleep(0.2)
        if _pid_alive(pid):
            # 只用 detach，不再先调 exports_sync.stop()。
            # 那个 RPC 要等游戏回包，游戏在退出流程里就永远等不到 —— 以前正是
            # 「等 4 秒 → 强杀收尸进程」这条链把带 agent 的进程硬杀掉，agent
            # 半卸不卸地留在游戏里，游戏这边就彻底动不了了。
            # detach 本身就是「卸载脚本 + 摘掉连接」，效果一样且不会卡在 RPC 上。
            hit = {"err": None}

            def _cleanup():
                try:
                    if session is not None:
                        session.detach()
                except Exception as e:
                    hit["err"] = e

            th = threading.Thread(target=_cleanup, name="frida-cleanup",
                                  daemon=True)
            th.start()
            wait_s = 2.0 if ("quit_ev" in locals() and quit_ev.is_set()) else 4.0
            th.join(timeout=wait_s)
            if th.is_alive():
                print("游戏没响应卸载请求，丢弃 Frida 连接（进程退出时自动清理）",
                      flush=True)
        else:
            print("目标游戏已退出，跳过 Frida 卸载请求", flush=True)
        clear_stop()
    if "nomod" in locals() and nomod["value"]:
        return 3          # 让控制台知道「这次没挂成，但游戏没问题，过会儿再来」
    return 0


if __name__ == "__main__":
    sys.exit(main())

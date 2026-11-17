#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗/影之幻境 · AI 陪玩（2026-10-27，插件内置模块）

设计：
  · 读 logs/deck_live.json（Frida 常驻写的实时状态）；
  · 检测事件：抽牌/出牌/牌库变化/生命变化；
  · 调 OpenAI 兼容接口（用户自己填 key/base/model，DeepSeek/方舟/本地 Ollama 都行）；
  · 生成 1~2 句短评，回调给 UI 气泡；
  · 语音：Edge TTS（云端、免费、0 显存）→ 临时 mp3 → MCI 播放 → 播完删除。

别人拿到插件：不填 key 就不调用，填了就能用；不联网就只出文字。
"""
import asyncio
import ctypes
import io
import json
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _user_dir():
    """配置放在用户目录，不随插件包分发（避免打包时泄漏 API Key）。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "影之诗助手")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


CONFIG_PATH = os.path.join(_user_dir(), "companion.json")
LEGACY_CONFIG = os.path.join(HERE, "companion_config.json")
LIVE_PATH = os.path.join(HERE, "logs", "deck_live.json")

DEFAULT_CONFIG = {
    "enabled": False,
    "api_key": "",
    "provider": "通义千问（DashScope）",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "model": "qwen3.8-flash",
    "tts_enabled": True,
    "voice": "zh-CN-XiaoxiaoNeural",
    "rate": "+8%",
    "max_per_min": 6,
    "style": "简短、有情绪、像一起打牌的朋友，不剧透对手隐藏信息",
    "api_history": [],
    "personality": "gentle",
}

# 四种性格：重点区分句式和情绪表达；建议内容仍必须服从当前局面。
PERSONALITIES = {
    "gentle": ("温柔体贴", "语气柔和耐心，先关心主人，再像并肩商量般给建议；失误时温和提醒一步，赢了具体夸操作，绝不挖苦。"),
    "lively": ("元气活力", "热情明快、爱起哄，短句有节奏，适度感叹；关键抽牌或漂亮交换时带气氛，建议积极直接，避免每句都喊。"),
    "tsundere": ("傲娇嘴硬", "先嘴硬后关心，偶尔说「才、才不是担心你」；提醒时装作嫌弃但讲清理由，赢了不情愿地承认打得不错，只傲娇不贬低主人。"),
    "toxic": ("毒舌（慎用）", "吐槽尖锐简短，只针对本回合具体操作、费用或交换；吐槽后必须给可执行建议，不人身攻击、不骂脏话、不脱离场面泛泛辱骂。"),
}

_KNOW = {"data": None}


def _knowledge():
    """影之诗黑话/梗知识库（companion_knowledge.json）。"""
    if _KNOW["data"] is None:
        try:
            with io.open(os.path.join(HERE, "companion_knowledge.json"),
                         encoding="utf-8") as fh:
                _KNOW["data"] = json.load(fh)
        except Exception:
            _KNOW["data"] = {}
    return _KNOW["data"]


def _personality_id():
    try:
        p = (load_config().get("personality") or "gentle")
    except Exception:
        p = "gentle"
    return p if p in PERSONALITIES else "gentle"


def _personality_text():
    return PERSONALITIES[_personality_id()][1]


def remember_api(cfg):
    """把当前 API 配置记进历史（AI 设置里可直接选回用过的）。"""
    try:
        key = (cfg.get("api_key") or "").strip()
        if not key:
            return
        item = {"provider": cfg.get("provider") or "",
                "base_url": cfg.get("base_url") or "",
                "model": cfg.get("model") or "",
                "api_key": key}
        cur = load_config()
        hist = list(cur.get("api_history") or [])
        hist = [h for h in hist if not (
            h.get("provider") == item["provider"]
            and h.get("base_url") == item["base_url"]
            and h.get("model") == item["model"])]
        hist.insert(0, item)
        cur["api_history"] = hist[:10]
        save_config(cur)
    except Exception:
        pass

# 语音备选（Edge TTS 在线音色，只保留女声）
VOICES = [
    ("zh-CN-XiaoxiaoNeural", "晓晓（女，活泼）"),
    ("zh-CN-XiaoyiNeural", "晓伊（女，温柔）"),
]

# 模型提供商预设（OpenAI 兼容接口）：选哪个就自动填 base_url + 可选模型列表
PROVIDERS = {
    "DeepSeek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "火山方舟": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-evolving",
        "models": [
            "glm-5-3-flash-260828",
            "glm-5-2-260617",
            "doubao-seed-evolving",
            "doubao-seed-2-1-pro-260628",
            "doubao-seed-2-1-turbo-260628",
            "doubao-seedance-1-0-pro-250528",
        ],
    },
    "OpenAI": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4o", "o3-mini"],
    },
    "Kimi（月之暗面）": {
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
    },
    "通义千问（DashScope）": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "models": ["qwen-plus", "qwen-max", "qwen3.8-flash"],
    },
    "智谱 GLM": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-flash",
        "models": ["glm-4-flash", "glm-4-plus", "glm-5-3-flash"],
    },
    "OpenRouter（含 Claude/GPT）": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "anthropic/claude-3.5-sonnet",
        "models": ["anthropic/claude-3.5-sonnet", "openai/gpt-4o-mini"],
    },
    "Ollama（本地）": {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:3b",
        "models": ["qwen2.5:3b", "qwen2.5:7b", "llama3.2:3b"],
    },
}


def load_config():
    cfg = {}
    try:
        with io.open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception:
        # 旧版配置在插件目录里：读一次并迁移到用户目录
        try:
            with io.open(LEGACY_CONFIG, encoding="utf-8") as fh:
                cfg = json.load(fh)
            if cfg:
                save_config(cfg)
        except Exception:
            cfg = {}
    base = dict(DEFAULT_CONFIG)
    base.update(cfg or {})
    return base


def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, CONFIG_PATH)


def _card_name(cid):
    try:
        import deck_reader
        meta = deck_reader.load_meta()
        m = meta.get(str(cid)) or {}
        return m.get("name") or ("#%d" % cid)
    except Exception:
        return "#%d" % cid


def _norm(cards):
    """手牌列表 → 名字列表（去重前保留重复）。"""
    return [_card_name(c) for c in (cards or []) if c]


def _guess_class():
    """从当前卡组推断我方职业（卡表 class 众数）。"""
    try:
        import deck_reader
        decks = deck_reader._decks_from_file()
        active = deck_reader._active_name()
        item = next((x for x in decks if x.get("name") == active),
                    decks[0] if decks else None)
        if not item:
            return ""
        meta = deck_reader.load_meta()
        from collections import Counter
        c = Counter()
        for cid in item["cards"]:
            v = meta.get(str(cid)) or {}
            cls = v.get("class")
            if cls:
                c[cls] += 1
        if not c:
            return ""
        return {0: "中立", 1: "精灵", 2: "皇家护卫", 3: "巫师",
                4: "龙族", 5: "梦魇", 6: "主教", 7: "复仇者"}.get(
            c.most_common(1)[0][0], "")
    except Exception:
        return ""


def explain_http_error(status, body):
    """将常见 API HTTP 错误转成可操作的中文原因。"""
    text = str(body or "")[:700]
    low = text.lower()
    if status in (401, 403):
        reason = "API Key 无效、已过期，或没有调用该模型的权限。请核对密钥和平台账号状态。"
    elif status == 404:
        reason = "接口地址或模型/接入点名称不存在。请核对 Base URL 和模型 ID。"
    elif status == 429:
        reason = "请求频率或账户额度已达限制。请稍后重试并检查模型额度。"
    elif status >= 500:
        reason = "模型服务端暂时故障。请稍后重试，或查看服务商状态。"
    elif "model" in low and any(x in low for x in ("not found", "does not exist", "不存在")):
        reason = "模型 ID 不存在或当前账号未开通，请核对模型/接入点名称。"
    elif "余额" in text or "billing" in low or "quota" in low:
        reason = "账户余额或模型额度不足，请检查服务商控制台。"
    else:
        reason = "服务商返回 HTTP 错误，请核对 API 配置和账户状态。"
    detail = " ".join(text.split())
    return "HTTP %s：%s%s" % (status, reason,
                              ("\n服务端信息：" + detail[:280]) if detail else "")


class Companion:
    """事件检测 + LLM 短评 + Edge TTS。"""

    def __init__(self, on_text=None, on_state=None):
        self.cfg = load_config()
        self.last_error = ""
        self.on_text = on_text or (lambda s: None)
        self.on_state = on_state or (lambda s: None)
        self._last = {}
        self._hand = []
        self._deck = None
        self._life = None
        self._last_call = 0.0
        self._last_turn = None
        self._in_battle = False      # 是否在对局中（用于对局结束检测）
        self._end_done = False       # 本局结束语是否已说过
        self._last_life = None
        self._lock = threading.Lock()
        self._busy = False
        self._tmp_audio = []
        self._cfg_mtime = 0.0
        self._deck_context_key = None
        self._deck_context = {"name": "", "cards": (), "class": ""}
        self._card_meta_key = None
        self._card_meta_cache = {}
        self.timing = {}
        self._http_session = None

    # ---------------- 状态读取 ----------------
    def _live(self):
        try:
            with io.open(LIVE_PATH, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None

    def _diff(self, snap):
        """返回事件列表。"""
        ev = []
        hand = _norm(snap.get("hand"))
        deck = snap.get("deck")
        life = snap.get("life")
        if self._deck is not None and deck is not None:
            if deck < self._deck:
                ev.append("抽了 %d 张牌" % (self._deck - deck))
            elif deck > self._deck:
                ev.append("有 %d 张牌回到了牌库" % (deck - self._deck))
        if self._life is not None and life is not None and life < self._life:
            ev.append("生命值掉了 %d 点（现在 %d）" % (self._life - life, life))
        old = list(self._hand)
        for name in hand:
            if name in old:
                old.remove(name)
            else:
                ev.append("抽到「%s」" % name)
        for name in old:
            ev.append("「%s」离开了手牌" % name)
        self._hand = hand
        self._deck = deck
        self._life = life
        return ev

    # ---------------- LLM ----------------
    def _deck_context_cached(self):
        """只在卡组文件或卡表变更时重新读取并分析当前卡组。"""
        try:
            import deck_reader
            decks_path = os.path.join(HERE, "gamedata", "user_decks.json")
            legacy_path = os.path.join(HERE, "gamedata", "user_deck.json")
            def _stamp(path):
                try:
                    st = os.stat(path)
                    return (st.st_mtime_ns, st.st_size)
                except OSError:
                    return None
            key = (_stamp(decks_path), _stamp(legacy_path),
                   _stamp(deck_reader.META_PATH))
            if key == self._deck_context_key:
                return self._deck_context
            decks = deck_reader._decks_from_file()
            active = deck_reader._active_name()
            item = next((x for x in decks if x.get("name") == active),
                        decks[0] if decks else None)
            meta = deck_reader.load_meta()
            from collections import Counter
            classes = Counter()
            cards = tuple(item.get("cards") or ()) if item else ()
            for cid in cards:
                card = meta.get(str(cid)) or {}
                cls = card.get("class")
                if cls:
                    classes[cls] += 1
            cls_id = classes.most_common(1)[0][0] if classes else None
            class_name = {0: "中立", 1: "精灵", 2: "皇家护卫", 3: "巫师",
                          4: "龙族", 5: "梦魇", 6: "主教", 7: "复仇者"}.get(
                              cls_id, "")
            self._deck_context = {"name": item.get("name", "") if item else "",
                                  "cards": cards, "class": class_name}
            self._deck_context_key = key
        except Exception:
            # 读取失败时保留最近一次有效快照，不在每个回合重复扫描。
            pass
        return self._deck_context

    def _prompt(self, snap, events, hand_names):
        """只用本回合相关数据组短 prompt；卡牌元数据由 deck_reader 缓存。"""
        import deck_reader
        meta = self._card_meta_cache

        def card(cid):
            return meta.get(str(cid)) or {}

        def describe(unit):
            cid = unit.get("cardId")
            m = card(cid)
            name = m.get("name") or _card_name(cid)
            atk, life = int(unit.get("atk") or 0), int(unit.get("life") or 0)
            return "%s %d/%d" % (name, atk, life)

        my_field = list(snap.get("field") or [])[:5]
        enemy = snap.get("enemy") or {}
        enemy_field = list(enemy.get("field") or [])[:5]
        my_txt = "、".join(describe(x) for x in my_field) or "空"
        enemy_txt = "、".join(describe(x) for x in enemy_field) or "空"
        turn = snap.get("turn")
        pp = snap.get("pp")
        maxpp = snap.get("maxpp")
        if pp is None:
            pp = maxpp if maxpp is not None else turn

        # 最多检查前 4 张手牌，只为找当前能用的解场牌，不扫描全手牌或全卡组。
        hand_ids = [c for c in (snap.get("hand") or [])[:4] if c]
        threats = [u for u in enemy_field
                   if int(u.get("atk") or 0) >= 3 or int(u.get("life") or 0) >= 4]
        removal_words = ("破坏", "消灭", "给予伤害", "对敌方随从")
        playable_removal = []
        key_cards = []
        for cid in hand_ids:
            m = card(cid)
            cost = int(m.get("cost") or 0)
            skill = " ".join((m.get("skill") or "").split())
            name = m.get("name") or _card_name(cid)
            if skill:
                key_cards.append("%s(%s费)%s" % (name, cost, skill[:18]))
            if cost <= int(pp or 0) and any(word in skill for word in removal_words):
                playable_removal.append(name)

        advice = []
        if threats and playable_removal:
            target = max(threats, key=lambda u: int(u.get("atk") or 0))
            advice.append("有够费解场牌%s，可考虑处理对手%s" %
                          (playable_removal[0], _card_name(target.get("cardId"))))
        elif threats and my_field:
            advice.append("对手有高威胁随从，评估交换并留意下回合承伤")
        elif threats:
            advice.append("对手有威胁随从，留意守脸或等待解场")
        if enemy_field:
            enemy_attack = sum(int(u.get("atk") or 0) for u in enemy_field)
            if enemy_attack >= 6 and int(snap.get("life") or 20) <= 10:
                advice.insert(0, "我方血量低且对手场攻高，优先解场或守脸")
        deck_context = self._deck_context
        personality = PERSONALITIES.get(
            self.cfg.get("personality") or "gentle", PERSONALITIES["gentle"])[1]
        lines = [
            "鲸鲸口吻：" + personality,
            "只依据场面和手牌说一句；有具体建议时点名牌或目标，否则评论刚发生的事。",
            "我方%s 费用%s/%s 生命%s；场上：%s。" % (
                deck_context.get("class") or "", pp, maxpp, snap.get("life"), my_txt),
            "对手场上：%s；对手生命%s。" % (enemy_txt, enemy.get("life", "?")),
            "手牌：%s；牌库%s；回合%s。" % (
                "、".join(hand_names[:5]) if hand_names else "空",
                snap.get("deck"), turn),
        ]
        if advice:
            lines.append("场面判断：" + "；".join(advice[:1]))
        elif key_cards:
            lines.append("可用手牌：" + key_cards[0])
        if events:
            lines.append("刚发生：" + "；".join(events[:1]))
        lines.append("限 24 字，不编造事实，不给与当前局面无关的泛化建议。")
        return "\n".join(lines)

    def _call_llm(self, prompt, timeout=(8, 45), max_tokens=28,
                  on_chunk=None):
        """流式请求；记录连接、首段与完成耗时，并给出中文错误说明。"""
        import requests
        cfg = self.cfg
        key = (cfg.get("api_key") or "").strip()
        if not key:
            self.last_error = "未填写 API Key。请在 AI 设置中填写有效密钥。"
            return None
        url = (cfg.get("base_url") or "").rstrip("/") + "/chat/completions"
        headers = {"Authorization": "Bearer " + key,
                   "Content-Type": "application/json"}
        payload = {"model": cfg.get("model") or "deepseek-chat",
                   "messages": [{"role": "user", "content": prompt}],
                   "max_tokens": max_tokens, "temperature": 0.7,
                   "stream": True}
        started = time.monotonic()
        connected = None
        first = None
        pieces = []
        session = self._http_session
        if session is None:
            session = requests.Session()
            self._http_session = session
        try:
            r = session.post(url, json=payload, timeout=timeout,
                             headers=headers, stream=True)
            connected = time.monotonic()
            if r.status_code != 200:
                self.last_error = explain_http_error(r.status_code, r.text)
                return None
            for raw in r.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                if not raw.startswith("data:"):
                    continue
                chunk = raw[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                    choice = (obj.get("choices") or [{}])[0]
                    delta = (choice.get("delta") or {}).get("content")
                    if delta is None:
                        delta = (choice.get("message") or {}).get("content")
                except Exception:
                    continue
                if not delta:
                    continue
                if first is None:
                    first = time.monotonic()
                pieces.append(str(delta))
                if on_chunk:
                    on_chunk("".join(pieces))
            result = "".join(pieces).strip()
            if not result:
                # 兼容不返回 SSE 内容的 OpenAI 代理。
                payload["stream"] = False
                r = session.post(url, json=payload, timeout=timeout,
                                  headers=headers)
                connected = time.monotonic()
                if r.status_code != 200:
                    self.last_error = explain_http_error(r.status_code, r.text)
                    return None
                result = (r.json().get("choices") or [{}])[0].get(
                    "message", {}).get("content", "").strip()
                if result and on_chunk:
                    on_chunk(result)
            ended = time.monotonic()
            self.timing = {"api_first_byte": connected - started if connected else None,
                           "first_token": first - started if first else None,
                           "total": ended - started}
            print("AI 耗时：接口首包 %.2fs / 模型首段 %s / 总计 %.2fs" % (
                self.timing["api_first_byte"] or 0,
                ("%.2fs" % self.timing["first_token"]
                 if self.timing["first_token"] is not None else "无首段"),
                self.timing["total"]), flush=True)
            self.last_error = ""
            return result or None
        except Exception as e:
            try:
                import requests.exceptions as exc
                if isinstance(e, exc.ConnectTimeout):
                    msg = "连接超时：无法及时连接 API。请检查网络、DNS 或代理。"
                elif isinstance(e, exc.ReadTimeout):
                    msg = "模型响应超时：API 已连接，但模型未及时返回。可检查接入点状态或更换快速模型。"
                elif isinstance(e, exc.ConnectionError):
                    msg = "网络连接失败：请检查网络、代理和 API 地址。"
                elif isinstance(e, exc.SSLError):
                    msg = "TLS 安全连接失败：检查系统时间、代理证书或网络拦截。"
                else:
                    msg = "请求失败（%s）：%s" % (type(e).__name__, str(e)[:180])
            except Exception:
                msg = "请求失败：%s" % str(e)[:180]
            self.timing = {"api_first_byte": (connected - started) if connected else None,
                           "first_token": (first - started) if first else None,
                           "total": time.monotonic() - started}
            self.last_error = msg
            return None

    # ---------------- Edge TTS ----------------
    def _pet_action(self, action):
        """让宠物播放一个动作（跨进程：写 logs/_pet_cmd.json，宠物轮询读取）。"""
        try:
            import json as _json
            p = os.path.join(HERE, "logs", "_pet_cmd.json")
            tmp = p + ".tmp"
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                _json.dump({"action": action, "at": time.time()}, fh,
                           ensure_ascii=False)
            os.replace(tmp, p)
        except Exception:
            pass

    def _speak(self, text):
        if not self.cfg.get("tts_enabled"):
            return
        text = (text or "").strip()
        if not text:
            return
        voice = self.cfg.get("voice") or "zh-CN-XiaoxiaoNeural"
        rate = self.cfg.get("rate") or "+0%"
        fd, path = tempfile.mkstemp(suffix=".mp3", prefix="coo_")
        os.close(fd)
        try:
            import edge_tts

            async def _gen():
                c = edge_tts.Communicate(text, voice, rate=rate)
                await c.save(path)

            asyncio.run(_gen())
            if os.path.getsize(path) > 0:
                play_mp3_blocking(path)
        except Exception:
            pass
        finally:
            try:
                os.remove(path)
            except Exception:
                pass

    # ---------------- 主循环 ----------------
    def tick(self):
        # 配置仅在文件变更时重读，避免每 2 秒重复解析磁盘文件。
        try:
            mt = os.path.getmtime(CONFIG_PATH)
            if mt != self._cfg_mtime:
                self.cfg = load_config()
                self._cfg_mtime = mt
        except Exception:
            pass
        if not self.cfg.get("enabled"):
            return
        if self._busy:
            return
        snap = self._live()
        if not snap:
            self._maybe_end_battle()
            return
        age = time.time() - float(snap.get("at") or 0) / 1000.0
        if age > 30:
            self._maybe_end_battle()
            return
        # 换牌阶段不开口，等他确定手牌再说
        if not snap.get("mulliganDone"):
            return
        # 记录战况（结束判断用）：进入对局、记最后生命
        self._in_battle = True
        self._last_life = snap.get("life")
        if self._end_done:
            self._end_done = False
        # 每个回合最多说一次
        turn = snap.get("turn")
        if turn is not None and turn == self._last_turn:
            return
        self._last_turn = turn
        with self._lock:
            events = self._diff(snap)
        hand_names = _norm(snap.get("hand"))
        if not events and not hand_names:
            return
        now = time.time()
        # 播报降频：平时 6 秒内最多一次；普通出牌 18 秒内最多一次（用户 2026-10-29 要求多说）
        if now - self._last_call < 6.0:
            return
        important = any(("抽到" in e) or ("掉了" in e) or ("回到了牌库" in e)
                        for e in events)
        if not important and now - self._last_call < 18.0:
            return
        self._last_call = now
        prompt = self._prompt(snap, events, hand_names)
        self._busy = True

        def _work():
            try:
                line = self._call_llm(
                    prompt, on_chunk=lambda text: self.on_text(text))
                if line:
                    print("AI 说：%s" % line, flush=True)
                    try:
                        import random as _rnd
                        if _rnd.random() < 0.5:
                            self._pet_action(_rnd.choice(
                                ["喷水", "开盖", "自拍", "番茄酱"]))
                    except Exception:
                        pass
                    try:
                        with open(os.path.join(HERE, "logs",
                                               "companion.log"), "a",
                                  encoding="utf-8") as fh:
                            fh.write("%s %s\n"
                                     % (time.strftime("%H:%M:%S"), line))
                    except Exception:
                        pass
                    self.on_text(line)
                    threading.Thread(target=self._speak, args=(line,),
                                     daemon=True).start()
                else:
                    # 没 key / 请求失败：只写日志，不往宠物气泡上弹错误
                    # （用户 2026-10-29：戳模型时只会显示错误 → 错误不再覆盖气泡）
                    err = getattr(self, "last_error", "") or "未知原因"
                    print("AI 未接入：%s" % err, flush=True)
                    try:
                        with open(os.path.join(HERE, "logs",
                                               "companion-err.log"), "a",
                                  encoding="utf-8") as fh:
                            fh.write("%s AI 未接入：%s\n"
                                     % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                        err))
                    except Exception:
                        pass
            finally:
                self._busy = False

        threading.Thread(target=_work, daemon=True).start()

    def _end_prompt(self, win):
        """对局结束的告别语：输了鼓励，赢了按性格夸/嘲讽。"""
        lines = [
            "你是「鲸鲸」，二头身鲸鱼女仆娘，说话用气泡，一次一句。",
            "性格：%s" % _personality_text(),
        ]
        if win:
            lines.append(
                "这局赢了。按性格说一句：温柔/元气就夸主人；傲娇可以嘲讽对手，"
                "用原句梗（「弱小的家伙没资格玩影之诗」「输给你的人都不玩影之诗了」）。")
        else:
            lines.append(
                "这局输了。鼓励主人一句、别嘲讽（「下一把稳的」「别灰心」这种）。")
        lines.append("只说 1 句、15~30 字，别自称 AI。")
        return "\n".join(lines)

    def _maybe_end_battle(self):
        """对局数据过期（10 秒无更新）→ 认为一局结束，按胜负说一句。"""
        if not getattr(self, "_in_battle", False):
            return
        if getattr(self, "_end_done", False):
            return
        self._in_battle = False
        self._end_done = True
        life = getattr(self, "_last_life", None)
        win = life is not None and life > 0
        prompt = self._end_prompt(win)
        self._busy = True

        def _work():
            try:
                line = self._call_llm(
                    prompt, on_chunk=lambda text: self.on_text(text))
                if line:
                    self.on_text(line)
                    try:
                        threading.Thread(target=self._speak, args=(line,),
                                         daemon=True).start()
                    except Exception:
                        pass
            finally:
                self._busy = False

        threading.Thread(target=_work, daemon=True).start()

    def test_llm(self):
        """测试连接：短超时 + 短回复，别让用户等太久。"""
        return self._call_llm("只回复两个字：在线", timeout=(5, 12),
                              max_tokens=12)

    def test_tts(self, text="陪玩语音测试，听得见吗？"):
        self._speak(text)


# ---------------- Windows 播放 ----------------
def play_mp3_blocking(path):
    """用 MCI 播放 mp3，阻塞到播完（零依赖）。"""
    try:
        mci = ctypes.windll.winmm.mciSendStringW
        alias = "coo_play_%d" % (int(time.time() * 1000) % 100000)
        buf = ctypes.create_unicode_buffer(256)
        mci('open "%s" type mpegvideo alias %s' % (path, alias), buf, 256, 0)
        mci("play %s wait" % alias, buf, 256, 0)
        mci("close %s" % alias, buf, 256, 0)
    except Exception:
        pass


def main():
    """命令行自测：python companion.py test"""
    cfg = load_config()
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        c = Companion(on_text=lambda s: print("[AI]", s))
        print("cfg:", {k: ("***" if k == "api_key" and v else v)
                       for k, v in cfg.items()})
        if cfg.get("api_key"):
            print("llm:", c.test_llm())
        else:
            print("llm: 未填 api_key")
        c.test_tts()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "voices":
        for v, n in VOICES:
            print(v, n)
        return
    print("用法: companion.py test | voices")


if __name__ == "__main__":
    main()

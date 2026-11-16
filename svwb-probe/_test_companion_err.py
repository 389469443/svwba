#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 陪玩接入错误提示自测：区分「没填 key」和「请求失败」，并检查模型预设。"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS = 0
FAIL = 0


def check(name, ok, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("  PASS", name, extra)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


class FakeResp:
    def __init__(self, code=400, text='{"error":"invalid model"}',
                 content="ok"):
        self.status_code = code
        self.text = text
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}

    def iter_lines(self, decode_unicode=False):
        # 流式 SSE：companion 现在走 stream=True
        yield ('data: ' + '{"choices":[{"delta":{"content":"%s"}}]}' % self._content)
        yield "data: [DONE]"


class FakeRequests:
    def __init__(self):
        self.resp = FakeResp()

    def post(self, *a, **k):
        return self.resp

    def Session(self):
        # companion 现在复用 requests.Session()；fake 直接返回自身
        return self


def main():
    fake = FakeRequests()
    sys.modules["requests"] = fake
    import companion

    c = companion.Companion()

    c.cfg = {"api_key": "k", "base_url": "https://x/y", "model": "m"}
    r = c._call_llm("hi")
    check("HTTP 400 记下具体错误",
          r is None and "HTTP 400" in (c.last_error or ""), c.last_error)

    c.cfg = {"api_key": "", "base_url": "https://x/y", "model": "m"}
    r = c._call_llm("hi")
    check("没填 key 单独提示", r is None and ("API Key" in (c.last_error or "")),
          c.last_error)

    fake.resp = FakeResp(200)
    c.cfg = {"api_key": "k", "base_url": "https://x/y", "model": "m"}
    r = c._call_llm("hi")
    check("成功返回文本", r == "ok", r)

    names = list(companion.PROVIDERS.keys())
    check("预设含火山方舟", any("火山方舟" in n for n in names))
    seed_models = []
    for _p in companion.PROVIDERS.values():
        seed_models += list(_p.get("models") or [])
    check("火山方舟模型含 Seed-Evolving",
          any("seed-evolving" in m for m in seed_models))
    check("火山方舟模型含 Seedance",
          any("seedance" in m for m in seed_models))

    print()
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统计服务器连接状态（门控）+ 本地缓存/队列（2026-10-19）

用户规则（2026-10-19）：
  · **只有连接服务器后才会收集数据，否则都不显示统计页**。
  · 统计页上常驻「数据不足，仅供参考」。

所以本模块只做三件事：
  1. 读/写 `logs/meta-settings.json`（enabled / server / device_id / connected）；
  2. 判定 `available()` —— 启用 + 有地址 + 最近连上过（默认 1 小时内）；
  3. 连接探测 / 缓存 / 上报队列（都支持注入 transport，方便离线自测）。

⚠️ 没有服务器时，`probe()` 会失败，`available()` 为 False，
   于是控制台不创建统计入口、判定器也不写上报队列 —— 符合用户要求。
"""
import io
import json
import os
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
SETTINGS = os.path.join(LOGS, "meta-settings.json")
CACHE = os.path.join(LOGS, "meta_cache.json")
QUEUE = os.path.join(LOGS, "upload_queue.jsonl")

# 连接有效期：超过这个时间没成功连过，就当作"没连"（不显示统计页）
CONNECT_TTL = 3600


def _default():
    return {"enabled": False, "server": "", "device_id": "", "auto_upload": True,
            "connected": False, "connected_at": 0.0}


def _read_json(path, default):
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else default
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    """原子写（tmp + os.replace），与项目其它设置文件一致。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.tmp-%d" % (path, os.getpid())
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def load_settings(path=SETTINGS):
    d = _default()
    d.update(_read_json(path, {}))
    if not d.get("device_id"):
        d["device_id"] = str(uuid.uuid4())
        _write_json(path, d)      # 首次生成就落盘，避免每局换 id
    return d


def save_settings(d, path=SETTINGS):
    return _write_json(path, d)


def state(path=SETTINGS, now=None):
    """返回门控状态。available=False 时控制台不显示统计页。"""
    now = time.time() if now is None else float(now)
    d = load_settings(path)
    enabled = bool(d.get("enabled"))
    server = str(d.get("server") or "").strip()
    connected = bool(d.get("connected"))
    fresh = (now - float(d.get("connected_at") or 0)) < CONNECT_TTL
    available = bool(enabled and server and connected and fresh)
    if not enabled:
        reason = "未启用"
    elif not server:
        reason = "未填服务器地址"
    elif not connected:
        reason = "未连接"
    elif not fresh:
        reason = "连接已过期"
    else:
        reason = "已连接"
    return {"enabled": enabled, "server": server,
            "connected": connected, "fresh": fresh,
            "available": available, "reason": reason,
            "device_id": d.get("device_id"), "auto_upload": bool(d.get("auto_upload", True))}


def available(path=SETTINGS):
    return bool(state(path)["available"])


def should_collect(path=SETTINGS):
    """判定器上报前必须过这一关：没连服务器就不收集。"""
    return available(path)


def _urllib_transport(url, timeout=5):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "svwb-meta/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read(65536)


def probe(server, path=SETTINGS, transport=None, now=None):
    """
    探测服务器是否可用：GET <server>/health（或 /meta/health）。
    成功 → 写 connected=True + connected_at。失败 → 写 connected=False。
    返回 True/False；任何异常都吞掉（不能因为服务器挂了影响判定器）。
    """
    now = time.time() if now is None else float(now)
    transport = transport or _urllib_transport
    server = str(server or "").strip().rstrip("/")
    d = load_settings(path)
    ok = False
    if server:
        for ep in ("/health", "/meta/health"):
            try:
                status, body = transport(server + ep)
                if int(status) != 200:
                    continue
                try:
                    j = json.loads(body.decode("utf-8", "replace") if isinstance(body, bytes) else body)
                except ValueError:
                    j = {}
                if j.get("ok") is False:
                    continue
                ok = True
                break
            except Exception:
                continue
    d["enabled"] = bool(d.get("enabled"))
    d["server"] = server
    d["connected"] = ok
    d["connected_at"] = now if ok else 0.0
    save_settings(d, path)
    return ok


def load_cache(path=CACHE):
    d = _read_json(path, {})
    return d or None


def save_cache(d, path=CACHE):
    return _write_json(path, d)


def load_queue(path=QUEUE):
    rows = []
    if not os.path.isfile(path):
        return rows
    try:
        with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        pass
    except OSError:
        pass
    return rows


def queue_row(row, path=QUEUE, limit=500, settings_path=SETTINGS):
    """只有连上服务器才入队（用户规则）。返回 True 表示已写。"""
    if not should_collect(settings_path):
        return False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rows = load_queue(path)
        if len(rows) >= limit:
            rows = rows[-limit:]
        with io.open(path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False

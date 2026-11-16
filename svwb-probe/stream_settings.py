"""各窗口共用控制台的直播模式。"""
import ctypes
import json
from pathlib import Path

FILE = Path(__file__).resolve().parent.parent / "dsh-shadowverse-huanjing/shots/live/overlay-settings.json"


def watch(root, change):
    previous = None
    def refresh():
        nonlocal previous
        try:
            enabled = bool(json.loads(FILE.read_text(encoding="utf-8-sig")).get("liveMode"))
        except FileNotFoundError:
            enabled = False
        except (ValueError, OSError):
            enabled = previous
        try:
            if enabled is not None and enabled != previous:
                change(enabled)
                previous = enabled
        finally:
            root.after(600, refresh)
    root.after_idle(refresh)


def capture_style(window):
    from ctypes import wintypes
    u = ctypes.windll.user32
    hwnd = wintypes.HWND(int(window.frame(), 0))
    flags = u.GetWindowLongW(hwnd, -20)
    desired = (flags & ~(0x80000 | 0x80)) | 0x40000
    if flags != desired:
        u.SetWindowLongW(hwnd, -20, desired)
        u.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x37)

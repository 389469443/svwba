# -*- coding: utf-8 -*-
"""
判断「游戏窗口是不是前台」，供三个面板决定要不要置顶。

用户要求：面板只显示在**游戏上面**，不要显示在所有窗口最上面。

## 之前的 bug
原来按窗口**标题**里是否含「影之诗」来找游戏窗口，结果把
「# 影之诗·影之幻境 AI辅助插件 — DeepSeek Harness」这个浏览器窗口当成了游戏，
于是面板永远判定"游戏在前台"，永远置顶。

## 现在的判据
按**进程可执行文件路径**识别：
  · 路径里含 `Shadowverse` 或 `MuMu` → 游戏本体
  · 标题里含 `影之诗` 也可以，但要**排除**浏览器 / 资源管理器 / python 自己
"""
import ctypes
import ctypes.wintypes as wt

_k32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 可执行文件路径里出现这些 → 认为是游戏
EXE_KEYS = ("shadowverse", "mumu")

# 标题里出现这些 → 一律排除（哪怕标题含"影之诗"）
TITLE_BLOCK = ("deepseek", "harness", "chrome", "edge", "firefox",
               "文件资源管理器", "explorer", "visual studio", "notepad")

# 进程名里出现这些 → 排除（我们自己的窗口）
EXE_BLOCK = ("python.exe", "pythonw.exe", "explorer.exe", "code.exe")


def _exe_of(hwnd):
    """返回 (pid, 小写 exe 全路径)"""
    pid = wt.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return 0, ""
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return pid.value, ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        ok = _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return pid.value, (buf.value.lower() if ok else "")
    finally:
        _k32.CloseHandle(h)


def _title_of(hwnd):
    n = _user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def find_game_hwnd():
    """返回游戏顶层窗口句柄；找不到返回 0"""
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        title = _title_of(hwnd)
        if not title:
            return True
        low_title = title.lower()
        if any(b in low_title for b in TITLE_BLOCK):
            return True
        pid, exe = _exe_of(hwnd)
        if not exe:
            return True
        if any(exe.endswith(b) for b in EXE_BLOCK):
            return True
        hit = any(k in exe for k in EXE_KEYS) or ("影之诗" in title) or ("mumu" in low_title)
        if not hit:
            return True
        r = wt.RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w >= 800 and h >= 500:
            found.append((hwnd, pid))
        return True

    _user32.EnumWindows(_cb, 0)
    return found[0][0] if found else 0


def game_is_foreground():
    """
    游戏是否在前台。

    返回 True  → 面板应该置顶（压在游戏上）
    返回 False → 面板应该取消置顶（被别的窗口盖住）
    """
    try:
        game = find_game_hwnd()
        if not game:
            return False
        fg = _user32.GetForegroundWindow()
        if not fg:
            return False
        fg_pid, fg_exe = _exe_of(fg)
        game_pid, game_exe = _exe_of(game)
        if game_pid and fg_pid == game_pid:
            return True
        # 有的模拟器前台窗口是它的子窗口/边框，进程可能不同，
        # 但 exe 路径相同 → 也算游戏在前台
        if game_exe and fg_exe and fg_exe == game_exe:
            return True
        return False
    except Exception:
        return False


# ── Z 序：贴在游戏正上方，但仍在**普通窗口层** ─────────────────────
GW_HWNDPREV = 0x0003
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
HWND_TOP = 0
HWND_NOTOPMOST = -2


def sync_zorder(hwnd):
    """
    更新面板的 Z 序。

    规则（照用户的两条要求同时满足）：
      · **游戏在前台** → 面板置顶，保证能压在**全屏游戏画面**上（实测：
        只做"插到游戏上方"的普通层摆放，会被全屏游戏不断顶到下面，看不见）
      · **游戏不在前台**（用户把浏览器/别的窗口拉上来）→ 立刻取消置顶，
        于是那个窗口自然把面板盖住

    游戏窗口的识别按**进程 exe 路径**（见 find_game_hwnd），
    不会再把浏览器标题里的"影之诗"当成游戏。
    """
    if not hwnd:
        return False
    flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER
    try:
        # ⚠️ SetWindowPos 的第二个参数有符号性：c_int 的 -1 会变成 0xFFFFFFFF，
        #    不是合法的 HWND_TOPMOST 常量（指针宽度 0xFFFFFFFFFFFFFFFF），
        #    调用会**静默失败**。必须显式声明类型并用 wt.HWND 传。
        _user32.SetWindowPos.argtypes = [
            wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wt.UINT]
        _user32.SetWindowPos.restype = wt.BOOL
        # 无论如何都不置顶：置顶会盖住浏览器（用户明确反对）
        if not find_game_hwnd():
            _user32.SetWindowPos(hwnd, wt.HWND(HWND_NOTOPMOST), 0, 0, 0, 0, flags)
            return False
        # ① 挂成游戏窗口的**从属窗口**：从属窗口永远压在宿主之上，
        #    但仍属普通窗口层 —— 于是"压在游戏上"和"浏览器能盖住"同时成立。
        attach_to_game(hwnd)
        # ② 永远不进置顶层（一旦置顶就会盖住浏览器）
        _user32.SetWindowPos(hwnd, wt.HWND(HWND_NOTOPMOST), 0, 0, 0, 0, flags)
        return True
    except Exception:
        return False


def hwnd_of(root):
    """Tk 顶层窗口的 Win32 句柄（客户区句柄的父窗口）"""
    try:
        return _user32.GetParent(root.winfo_id())
    except Exception:
        return 0

GWLP_HWNDPARENT = -8


def attach_to_game(hwnd):
    """
    把面板设成**游戏窗口的从属窗口**（owned window）。

    为什么用这个而不是置顶：
      · 从属窗口**始终压在它的宿主窗口之上** —— 所以面板一定显示在游戏画面上
      · 但它仍属于**普通窗口层** —— 用户把浏览器拉上来时，浏览器照样盖住面板
    这正好同时满足用户的两条要求，而且不需要"游戏是否在前台"这种不可靠的判断。

    返回 True 表示已挂上。
    """
    if not hwnd:
        return False
    try:
        game = find_game_hwnd()
        if not game:
            return False
        _user32.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_void_p]
        _user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        _user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, game)
        return True
    except Exception:
        return False

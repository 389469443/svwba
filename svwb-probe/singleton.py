#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 单实例锁（命名互斥量）。同一名字只有一个进程能拿到锁。"""
import ctypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ERROR_ALREADY_EXISTS = 183

# ⚠️ 必须声明 argtypes/restype：不声明时 ctypes 不知道怎么把 Python 的 str
#    转成 wchar_t*，CreateMutexW 会直接抛 TypeError，被下面的 except 吞掉、
#    当成「拿到锁」放行 —— 于是 _handles 里什么都没存，release/has 全成了
#    空操作。单实例保护形同虚设，而且「关掉再打开」看着正常纯属巧合。
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                   ctypes.c_wchar_p]
_kernel32.CreateMutexW.restype = ctypes.c_void_p
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.CloseHandle.restype = ctypes.c_int

# 保存句柄，进程退出前不要释放（daemon 进程退出时系统自动释放）
_handles = {}


def _key(name):
    return name if name.startswith("Global\\") else "Global\\" + name


def acquire(name):
    """拿到锁返回 True；已有别的实例返回 False。"""
    key = _key(name)
    try:
        ctypes.set_last_error(0)
        h = _kernel32.CreateMutexW(None, False, key)
        err = ctypes.get_last_error()
        if not h:
            # 5 = ERROR_ACCESS_DENIED：同名的 Global 互斥量已经被**更高权限**的
            # 进程（比如以管理员启动的控制台）建好了，普通权限连句柄都拿不到。
            # 这就是「已经在运行」，不能再放行，否则两个实例会同时开窗口。
            return err != 5
        if err == ERROR_ALREADY_EXISTS:
            try:
                _kernel32.CloseHandle(h)
            except Exception:
                pass
            return False
        _handles[key] = h
        return True
    except Exception:
        # 拿不到 API 就放行，不要因为锁把功能卡死
        return True


def release(name):
    """放掉锁（窗口关掉时要调，否则同一进程再开会被自己挡住）。

    CreateMutex 拿到的句柄在进程活着期间一直有效，所以同一进程第二次
    acquire 会拿到 ERROR_ALREADY_EXISTS。「关掉再打开」必须显式释放，
    否则会一直以为自己还在运行 —— 这正是「关掉记牌器后按钮打不开、
    要重启控制台才行」的原因。
    """
    key = _key(name)
    h = _handles.pop(key, None)
    if not h:
        return False
    try:
        _kernel32.CloseHandle(h)
        return True
    except Exception:
        return False


def has(name):
    """当前进程是不是还攥着这把锁。"""
    return _key(name) in _handles

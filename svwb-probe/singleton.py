#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 单实例锁（命名互斥量）。同一名字只有一个进程能拿到锁。"""
import ctypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
from ctypes import wintypes

_kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.OpenMutexW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_kernel32.OpenMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
ERROR_ALREADY_EXISTS = 183

# 保存句柄，进程退出前不要释放（daemon 进程退出时系统自动释放）
_handles = {}


def acquire(name, *, strict=False):
    """拿到锁返回 True；已有别的实例返回 False。"""
    key = name if name.startswith("Global\\") else "Global\\" + name
    try:
        ctypes.set_last_error(0)
        h = _kernel32.CreateMutexW(None, False, key)
        err = ctypes.get_last_error()
        if not h:
            if strict:
                raise ctypes.WinError(err)
            return True
        if err == ERROR_ALREADY_EXISTS:
            try:
                _kernel32.CloseHandle(h)
            except Exception:
                pass
            return False
        _handles[key] = h
        return True
    except Exception:
        if strict:
            raise
        # 拿不到 API 就放行，不要因为锁把功能卡死
        return True

def frida_running():
    """查询全局读取器锁，不创建锁，也不依赖快照是否新鲜。"""
    h = _kernel32.OpenMutexW(0x00100000, False, r"Global\SvwbFrida")
    if not h:
        return ctypes.get_last_error() == 5
    _kernel32.CloseHandle(h)
    return True


def release(name):
    """Release only a mutex handle owned by this process."""
    key = name if name.startswith("Global\\") else "Global\\" + name
    handle = _handles.pop(key, None)
    if handle:
        _kernel32.CloseHandle(handle)

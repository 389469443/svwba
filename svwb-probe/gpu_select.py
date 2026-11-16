"""显卡选择：让助手（Live2D 宠物 / 字幕 / 控制台）使用指定 GPU。

原理
----
Windows 10/11 的「图形设置」每个程序的首选 GPU 存在：
    HKCU\\Software\\Microsoft\\DirectX\\UserGpuPreferences
    值名 = 程序完整路径，值 = "GpuPreference=2;"（2=高性能独显，1=省电核显）

NVIDIA / AMD 驱动会读这个键；Live2D 走 OpenGL，较新的驱动同样生效。
**改完必须重启助手进程**（pythonw.exe 已经跑起来了，寄存器在启动时读取）。

这个模块只读写用户自己的注册表键，不需要管理员权限。
"""
import ctypes
import os
import subprocess
import sys
import winreg

REG_PATH = r"Software\Microsoft\DirectX\UserGpuPreferences"

AUTO = 0
POWER_SAVING = 1
HIGH_PERFORMANCE = 2

MODE_LABELS = {
    AUTO: "自动（系统决定）",
    POWER_SAVING: "省电（集成显卡）",
    HIGH_PERFORMANCE: "高性能（独立显卡）",
}

# 枚举时排除这些：虚拟显示器不是真 GPU
VIRTUAL_HINTS = ("virtual", "remote", "basic render", "mumu virtual",
                 "parsec", "displaylink", "spacedesk")


def list_gpus():
    """返回 [{'name':..., 'driver':..., 'vram_mb':int, 'vendor':..., 'kind':...}, ...]。

    只列**真实**显卡：MuMu / 远程桌面 / 基础显示适配器这类虚拟显示器
    没有显存、也没有视频处理器，直接按硬件特征排除（不靠猜名字）。
    这样换到任何用户的电脑上都能正确列出他自己的显卡。
    """
    out = []
    try:
        ps = ("Get-CimInstance Win32_VideoController | "
              "Select-Object Name,DriverVersion,AdapterRAM,VideoProcessor,Status | "
              "ConvertTo-Json -Compress")
        raw = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        import json
        data = json.loads(raw.stdout or "[]")
        if isinstance(data, dict):
            data = [data]
        for item in data:
            name = str(item.get("Name") or "").strip()
            if not name:
                continue
            low = name.lower()
            # 1) 名字特征：虚拟显示器/远程显示适配器
            if any(h in low for h in VIRTUAL_HINTS):
                continue
            # 2) 硬件特征：虚拟显示器没有显存、也没有视频处理器
            vram = item.get("AdapterRAM")
            try:
                vram_bytes = int(vram) if vram is not None else 0
            except (TypeError, ValueError):
                vram_bytes = 0
            proc = str(item.get("VideoProcessor") or "").strip()
            if vram_bytes <= 0 and not proc:
                continue
            try:
                vram_mb = int(vram_bytes / (1024 * 1024))
            except (TypeError, ValueError):
                vram_mb = 0
            out.append({
                "name": name,
                "driver": str(item.get("DriverVersion") or ""),
                "vram_mb": vram_mb,
                "vendor": proc,
                "kind": _guess_kind(name, proc, vram_mb),
            })
    except Exception:
        pass
    # 独显排前面（用户挑高性能时第一眼就能看到）
    out.sort(key=lambda g: (0 if g["kind"] == "discrete" else 1, -g["vram_mb"]))
    return out


def _guess_kind(name, proc, vram_mb):
    """区分独显 / 核显（只用于显示排序，不影响设置）。"""
    low = (name + " " + proc).lower()
    if any(k in low for k in ("nvidia", "geforce", "rtx", "gtx", "radeon rx",
                              "rx ", "arc a", "iris xe max")):
        return "discrete"
    if any(k in low for k in ("intel", "uhd graphics", "hd graphics",
                              "radeon(tm) graphics", "vega", "apu")):
        return "integrated"
    # 显存 >= 1GB 一般就是独显；小于 512MB 多是核显
    if vram_mb >= 1024:
        return "discrete"
    if vram_mb and vram_mb < 512:
        return "integrated"
    return "unknown"


def current_exe():
    """真正需要指定 GPU 的进程：pythonw.exe / python.exe。"""
    exe = sys.executable or ""
    return exe


def read_all_preferences():
    """读出注册表里所有已指定的程序 -> 偏好值。"""
    result = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH) as key:
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                result[name] = str(value)
                i += 1
    except OSError:
        pass
    return result


def parse_preference(text):
    """'GpuPreference=2;' -> 2；没有就返回 None。"""
    text = str(text or "")
    for part in text.split(";"):
        part = part.strip()
        if part.lower().startswith("gpupreference="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                return None
    return None


def get_preference(exe=None):
    """当前 exe 的偏好；没设置过返回 AUTO。"""
    exe = exe or current_exe()
    all_prefs = read_all_preferences()
    # 路径比较不区分大小写
    for name, value in all_prefs.items():
        if name.lower() == exe.lower():
            got = parse_preference(value)
            return AUTO if got is None else got
    return AUTO


def set_preference(exe, mode):
    """写 UserGpuPreferences；mode 取 AUTO / POWER_SAVING / HIGH_PERFORMANCE。

    返回 (ok, message)。AUTO 会删除该程序的键，让系统自己决定。
    """
    exe = exe or current_exe()
    if not exe:
        return False, "拿不到当前程序的路径"
    try:
        if mode == AUTO:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0,
                                    winreg.KEY_SET_VALUE) as key:
                    # 精确匹配键名（大小写不敏感）
                    for name in list(read_all_preferences()):
                        if name.lower() == exe.lower():
                            winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
            except OSError:
                pass
            return True, "已设为自动（系统决定）"
        if mode not in (POWER_SAVING, HIGH_PERFORMANCE):
            return False, "未知的显卡偏好: %r" % (mode,)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_PATH, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, exe, 0, winreg.REG_SZ,
                              "GpuPreference=%d;" % int(mode))
        return True, "已设为%s" % MODE_LABELS.get(mode, str(mode))
    except OSError as e:
        return False, "写入注册表失败：%s" % e


def describe(exe=None):
    """给设置界面用的一句话状态。"""
    exe = exe or current_exe()
    mode = get_preference(exe)
    gpus = list_gpus()
    lines = ["当前程序：%s" % (os.path.basename(exe) or "?")]
    if gpus:
        lines.append("检测到显卡：" + "、".join(g["name"] for g in gpus))
    lines.append("首选 GPU：%s" % MODE_LABELS.get(mode, str(mode)))
    if mode != AUTO:
        lines.append("（改完需要重启助手才生效）")
    return "\n".join(lines)


def apply_and_hint(mode):
    """设置并返回给用户看的提示文本。"""
    ok, msg = set_preference(current_exe(), mode)
    if not ok:
        return msg
    tip = ("\n\n已写入系统「图形设置」。\n"
           "重启助手后生效（托盘右键退出，再双击启动.bat）。")
    if mode == HIGH_PERFORMANCE:
        tip += ("\n如果还是核显在跑，可以在 NVIDIA 控制面板 → "
                "管理 3D 设置 → 程序设置里，把 pythonw.exe 指定为「高性能 NVIDIA 处理器」。")
    return msg + tip


if __name__ == "__main__":
    print(describe())
    for g in list_gpus():
        print("  - %s (%.0f MB, 驱动 %s)" % (g["name"], g["vram_mb"], g["driver"]))
    print("\n所有已指定 GPU 的程序:")
    for k, v in read_all_preferences().items():
        print("  %s -> %s" % (k, parse_preference(v)))

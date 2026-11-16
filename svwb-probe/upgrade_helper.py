"""从同级旧版迁移个人文件。成功后把旧目录改名为备份。"""
import hashlib
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
VERSION_DIR = re.compile(r"影之诗助手-v(\d+)\.(\d+)\.(\d+)")
DATA = ("gamedata/user_decks.json", "gamedata/user_deck.json",
        "logs/battle_result.json", "logs/history.json", "logs/console-settings.json",
        "logs/console-pos.json", "pet_config.json")


def profile(probe):
    key = hashlib.sha256(os.path.normcase(os.path.realpath(probe)).encode("utf-8")).hexdigest()[:16]
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "影之诗助手" / "installs" / key / "companion.json"


def copy_missing(src, dst):
    if not src.is_file() or dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dst.parent, delete=False) as fh:
        tmp = Path(fh.name)
    try:
        shutil.copyfile(src, tmp)
        if tmp.read_bytes() != src.read_bytes():
            raise OSError("源文件在迁移时发生变化，请关闭旧版后重试")
        try:
            tmp.rename(dst)  # Windows: existing destinations are never replaced.
        except FileExistsError:
            pass
    finally:
        tmp.unlink(missing_ok=True)


def migrate_version(root, check=False):
    probe = root / "svwb-probe"
    marker = probe / "logs" / ".upgrade-complete"
    match = VERSION_DIR.fullmatch(root.name)
    if not match or marker.exists():
        return
    current = tuple(map(int, match.groups()))
    candidates = []
    for old in root.parent.iterdir():
        m = VERSION_DIR.fullmatch(old.name)
        if m and old.is_dir() and old.resolve() == old and (old / "svwb-probe/app.py").is_file():
            version = tuple(map(int, m.groups()))
            if version < current:
                candidates.append((version, old))
    if not candidates:
        return
    _, old = max(candidates)
    source = old / "svwb-probe"
    backup = old.with_name(old.name + ".升级备份")
    if check:
        print("可迁移的旧版：", old)
        return
    from parent_guard import _alive
    for name in ("_app.pid", "frida_live.lock"):
        path = source / "logs" / name
        if path.is_file():
            pid = path.read_text().strip()
            if pid.isdigit() and _alive(int(pid)):
                raise OSError("请先关闭旧版助手和读取器，再启动新版")
    for rel in DATA:
        copy_missing(source / rel, probe / rel)
    config = profile(source)
    if not config.exists():
        previous = source / "companion.py"
        shared = profile(source).parents[2] / "companion.json"
        if previous.is_file() and '"installs"' not in previous.read_text("utf-8-sig") and shared.is_file():
            config = shared
        else:
            config = source / "companion_config.json"
    copy_missing(config, profile(probe))
    # 写标记后才改名；若改名失败则撤销标记，下一次可以重试。
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(old.name, encoding="utf-8")
    try:
        if backup.exists():
            backup = old.with_name(old.name + ".升级备份-" + os.urandom(4).hex())
        if old.resolve().parent != root.parent or backup.parent != root.parent:
            raise OSError("旧版目录不在安装目录中")
        old.rename(backup)
    except OSError:
        marker.unlink(missing_ok=True)
        raise
    print("旧版资料已迁移，原目录备份在", backup)


def upgrade(root, check=False):
    if check:
        return migrate_version(root, True)
    import msvcrt
    log_dir = root / "svwb-probe" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / ".upgrade.lock").open("a+b") as lock:
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        try:
            migrate_version(root)
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == "__main__":
    try:
        upgrade(ROOT, "--check" in sys.argv)
    except OSError as exc:
        print("升级未完成：", exc, file=sys.stderr)
        sys.exit(1)

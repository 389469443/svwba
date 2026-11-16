#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""影之诗 SaveData.db 解密工具（XOR）。

来源：反编译 `Wizard2.Application.SQLiteSaveLoadHelper`（热更 Release/Application.bytes）：
    CalculateEncodeDecode(byteArray, key): array[i] = byteArray[i] ^ key[i % 44]
- key 用 ENCODE_KEY_ARRAY，value 用 ENCODE_VALUE_KEY_ARRAY。
- XOR 对称，加密/解密同一个函数。

用途：只读解密 Persistent/d/SaveData.db（AppSetting 表），拿 ClientId / Uuid 等。
注意：**卡组不在这个库里**（只有设置），卡组走服务器 DeckGetList（明文 JSON）。

用法：
    python save_decrypt.py                     # 解默认路径
    python save_decrypt.py --db <路径>          # 指定 db
    python save_decrypt.py --dump              # 打印全部键值
"""
import argparse
import os
import shutil
import sqlite3
import sys
import tempfile

ENCODE_KEY = bytes([
    180, 105, 237, 76, 92, 225, 136, 167, 216, 229,
    203, 97, 192, 142, 192, 53, 193, 221, 70, 165,
    49, 28, 198, 100, 237, 69, 232, 171, 241, 154,
    187, 109, 230, 122, 149, 55, 155, 126, 86, 197,
    17, 249, 132, 26,
])
ENCODE_VALUE_KEY = bytes([
    134, 38, 143, 207, 119, 56, 79, 93, 35, 227,
    248, 157, 176, 190, 179, 242, 69, 50, 97, 51,
    198, 209, 58, 250, 40, 110, 176, 246, 163, 174,
    137, 142, 166, 200, 232, 158, 84, 254, 77, 228,
    150, 133, 205, 241,
])

DEFAULT_DB = (r"D:/Shadowverse/MuMu模拟器x影之诗高清版_Data/"
              r"Persistent/d/SaveData.db")


def xor_bytes(data, key):
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def decode(blob, key):
    if not blob:
        return ""
    if blob == b"\x00":
        return ""
    return xor_bytes(blob, key).decode("utf-8", "replace")


def load_pairs(db_path):
    """复制到临时目录再读（游戏可能正占用原文件）。"""
    tmp = os.path.join(tempfile.gettempdir(), "_sv_savedata_decrypt.db")
    shutil.copyfile(db_path, tmp)
    try:
        con = sqlite3.connect(tmp)
        rows = con.execute(
            "SELECT save_key, save_value FROM AppSetting").fetchall()
        out = {}
        for k, v in rows:
            ks = decode(k, ENCODE_KEY)
            vs = decode(v, ENCODE_VALUE_KEY)
            out[ks] = vs
        return out
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--dump", action="store_true", help="打印全部键值")
    args = ap.parse_args()
    if not os.path.exists(args.db):
        print("找不到 SaveData.db：" + args.db, file=sys.stderr)
        return 1
    data = load_pairs(args.db)
    if args.dump:
        for k in sorted(data):
            v = data[k]
            print("%s = %s" % (k, v[:200]))
        return 0
    for key in ("ClientId", "Uuid", "AuthKey", "CompletedResourceVersion"):
        print("%s = %s" % (key, data.get(key, "")))
    print("（共 %d 个键；--dump 看全部）" % len(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())

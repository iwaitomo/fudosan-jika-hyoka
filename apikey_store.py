# -*- coding: utf-8 -*-
"""APIキーの保存・読込（プロジェクト内の apikey.txt に平文保存）。
このファイルは .gitignore で共有対象外にしている。"""
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apikey.txt")


def load_key() -> str:
    # 環境変数優先
    env = os.environ.get("REINFOLIB_API_KEY")
    if env:
        return env.strip()
    if os.path.exists(_PATH):
        with open(_PATH, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def save_key(key: str):
    with open(_PATH, "w", encoding="utf-8") as f:
        f.write((key or "").strip())


def clear_key():
    if os.path.exists(_PATH):
        os.remove(_PATH)

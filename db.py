# -*- coding: utf-8 -*-
"""案件の保存・読み込み（SQLite）。要件14章。"""
import os
import json
import sqlite3
from datetime import datetime

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "cases.db")


def _conn():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                案件名 TEXT,
                所在 TEXT,
                地番 TEXT,
                地積 REAL,
                調査日 TEXT,
                メモ TEXT,
                payload TEXT,
                created_at TEXT
            )
        """)


def save_case(案件名, 所在, 地番, 地積, メモ, payload: dict):
    """payload には公示・取引・算定結果・補正など一式を JSON で入れる。"""
    init_db()
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO cases (案件名, 所在, 地番, 地積, 調査日, メモ, payload, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (案件名, 所在, 地番, 地積,
             datetime.now().strftime("%Y-%m-%d"),
             メモ,
             json.dumps(payload, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")),
        )
        return cur.lastrowid


def list_cases(keyword: str = ""):
    init_db()
    with _conn() as conn:
        if keyword:
            like = f"%{keyword}%"
            rows = conn.execute(
                """SELECT id, 案件名, 所在, 地番, 地積, 調査日, created_at
                   FROM cases
                   WHERE 案件名 LIKE ? OR 所在 LIKE ? OR 地番 LIKE ?
                   ORDER BY id DESC""",
                (like, like, like),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, 案件名, 所在, 地番, 地積, 調査日, created_at
                   FROM cases ORDER BY id DESC"""
            ).fetchall()
        return [dict(r) for r in rows]


def load_case(case_id: int):
    init_db()
    with _conn() as conn:
        row = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["payload"] = json.loads(d.get("payload") or "{}")
        return d


def delete_case(case_id: int):
    init_db()
    with _conn() as conn:
        conn.execute("DELETE FROM cases WHERE id=?", (case_id,))

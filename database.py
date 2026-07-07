"""
DB接続とスキーマ管理
SQLite(training.db)への接続取得と、初回起動時のテーブル自動作成を行う。
"""
import sqlite3
from flask import g

DATABASE = "training.db"


def get_db():
    """リクエストごとのDB接続を取得する(Flaskのgに保持して使い回す)"""
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        # 外部キー制約を有効化(SQLiteはデフォルト無効のため)
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    """リクエスト終了時にDB接続を閉じる"""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """テーブルが無ければ作成する(初回起動時に自動実行)"""
    conn = sqlite3.connect(DATABASE)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_grade TEXT NOT NULL,
            attendance_no INTEGER NOT NULL,
            name TEXT NOT NULL,
            UNIQUE(class_grade, attendance_no)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS training_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            reservation_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            checkin_at TEXT,
            checkout_at TEXT,
            duration_min INTEGER,
            overtime_min INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (reservation_id) REFERENCES reservations(id)
        )
        """
    )

    conn.commit()
    conn.close()


def init_app(app):
    """Flaskアプリにteardown処理を登録する"""
    app.teardown_appcontext(close_db)

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
            is_admin INTEGER NOT NULL DEFAULT 0,
            UNIQUE(class_grade, attendance_no)
        )
        """
    )

    # 既存DB(is_adminカラムが無い旧スキーマ)への後方互換マイグレーション
    columns = conn.execute("PRAGMA table_info(users)").fetchall()
    column_names = [col[1] for col in columns]
    if "is_admin" not in column_names:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")

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

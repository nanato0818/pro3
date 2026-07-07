# pro3

pro３の授業

## 概要

トレーニングルーム予約システム。学校のジム・トレーニングルームを生徒がスマホ・PCから予約し、
入退室の時間を記録できるWebアプリです。

主な機能:

- クラス学年・出席番号・名前による簡易登録／ログイン（パスワードなし）
- 月間カレンダーからの予約（開始・終了時刻を15分刻みで指定）
- 週7時間までの予約上限チェック
- 予約一覧表示・取り消し
- 入室〜退室のトレーニングセッション管理（経過時間タイマー、超過時のブラウザ通知）

技術構成: Flask + SQLite + Jinja2テンプレート + vanilla JS

現在のフェーズ: フェーズ1（グレーボックス。全機能を一通り動かす段階。デザインの磨き込みは次フェーズ）

## セットアップ手順

```
pip install -r requirements.txt
python app.py
```

起動後、ブラウザで以下にアクセスしてください。

```
http://localhost:5000
```

初回起動時に `training.db`（SQLiteデータベースファイル）が自動生成されます。
`training.db` はGit管理対象外（.gitignore）です。

## ディレクトリ構成

```
pro3/
├── app.py            # Flaskアプリ本体（ルート定義）
├── database.py        # DB接続・スキーマ作成
├── templates/         # Jinja2テンプレート
├── static/
│   ├── style.css
│   └── app.js
├── requirements.txt
└── training.db         # 実行時に自動生成（Git管理外）
```

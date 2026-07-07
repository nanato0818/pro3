"""
トレーニングルーム予約システム
Flaskアプリ本体。ルート定義とDB初期化を行う。
"""
import calendar
import csv
import io
import json
import os
from datetime import datetime, date, timedelta

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response

import database
from database import get_db

app = Flask(__name__)
app.secret_key = "training-room-app-secret-key-phase1"  # フェーズ1用の簡易シークレットキー

database.init_app(app)
database.init_db()

# クラス学年の選択肢(I1〜I5, M1〜M5, S1〜S5 の15択)
CLASS_GRADES = [f"{prefix}{n}" for prefix in ("I", "M", "S") for n in range(1, 6)]

# 予約可能時間帯(6:00〜22:00、15分刻み)
TIME_SLOTS = []
_t = datetime.strptime("06:00", "%H:%M")
_end = datetime.strptime("22:00", "%H:%M")
while _t <= _end:
    TIME_SLOTS.append(_t.strftime("%H:%M"))
    _t += timedelta(minutes=15)

WEEKLY_LIMIT_MIN = 420  # 週7時間制限(分)

# 管理者登録コード(登録時にこのコードを入力すると管理者になる)
# 本番環境では環境変数 ADMIN_CODE で別の値に上書きする
ADMIN_CODE = os.environ.get("ADMIN_CODE", "pro3-admin-2026")


# ---------------------------------------------------------
# 共通ヘルパー
# ---------------------------------------------------------

def login_required(view):
    """未ログインならログインページへリダイレクトするデコレータ"""
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("user_id") is None:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    """未ログインならログインページへ、非管理者ならhomeへリダイレクトするデコレータ"""
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("user_id") is None:
            return redirect(url_for("login"))
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()
        if user is None or not user["is_admin"]:
            flash("管理者権限が必要です。", "error")
            return redirect(url_for("home"))
        return view(*args, **kwargs)

    return wrapped


@app.context_processor
def inject_current_user():
    """テンプレートから現在ログイン中のユーザー(管理者判定含む)を参照できるようにする"""
    current_user = None
    user_id = session.get("user_id")
    if user_id is not None:
        db = get_db()
        current_user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return {"current_user": current_user}


def get_weekly_reserved_minutes(user_id, target_date_str):
    """
    指定日付が属する週(月曜起点)の、active予約の合計予約時間(分)を返す。
    将来「実績時間(training_logsの実働分)も含める」に変更する場合は、
    この関数だけを修正すればよいように判定ロジックをここに集約している。
    """
    db = get_db()
    target = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    monday = target - timedelta(days=target.weekday())
    sunday = monday + timedelta(days=6)

    rows = db.execute(
        """
        SELECT start_time, end_time FROM reservations
        WHERE user_id = ? AND status = 'active'
          AND date BETWEEN ? AND ?
        """,
        (user_id, monday.isoformat(), sunday.isoformat()),
    ).fetchall()

    total = 0
    for row in rows:
        start = datetime.strptime(row["start_time"], "%H:%M")
        end = datetime.strptime(row["end_time"], "%H:%M")
        total += int((end - start).total_seconds() // 60)
    return total


def format_minutes(total_min):
    """分数を「◯時間◯分」の文字列に変換する"""
    total_min = max(0, int(total_min))
    hours = total_min // 60
    minutes = total_min % 60
    if hours and minutes:
        return f"{hours}時間{minutes}分"
    if hours:
        return f"{hours}時間"
    return f"{minutes}分"


def get_room_capacity():
    """settingsテーブルからルーム定員を読む。未設定なら20を返す。"""
    db = get_db()
    row = db.execute(
        "SELECT value FROM settings WHERE key = 'room_capacity'"
    ).fetchone()
    if row is None or row["value"] is None:
        return 20
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 20


def find_full_slot(date_str, start_time, end_time):
    """
    [start_time, end_time) を15分刻みのスロットに分解し、各スロットについて
    その日付のactive予約(全ユーザー)がそのスロットを覆っている件数を数える。
    定員以上のスロットがあれば、最初に見つかった満員スロットの時刻を返す。
    無ければNoneを返す。
    将来、定員判定のロジックを変更する場合はこの関数だけを修正すればよい。
    """
    db = get_db()
    capacity = get_room_capacity()

    slots = [t for t in TIME_SLOTS if start_time <= t < end_time]
    if not slots:
        return None

    active_reservations = db.execute(
        """
        SELECT start_time, end_time FROM reservations
        WHERE date = ? AND status = 'active'
        """,
        (date_str,),
    ).fetchall()

    for slot in slots:
        count = 0
        for r in active_reservations:
            if r["start_time"] <= slot < r["end_time"]:
                count += 1
        if count >= capacity:
            return slot
    return None


def get_weekly_limit_minutes(user_id, target_date_str):
    """
    target_date_strが属する週の上限時間(分)を返す。
    基本は週7時間(420分)だが、前週(前週月曜〜日曜)のtraining_logsの
    超過時間(overtime_min)合計をペナルティとして差し引く。下限は0分。
    将来ペナルティ算出ロジックを変えたい場合はこの関数だけを修正すればよい。
    """
    db = get_db()
    target = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    this_monday = target - timedelta(days=target.weekday())
    prev_monday = this_monday - timedelta(days=7)
    prev_sunday = this_monday - timedelta(days=1)

    row = db.execute(
        """
        SELECT COALESCE(SUM(overtime_min), 0) AS total_overtime
        FROM training_logs
        WHERE user_id = ? AND date BETWEEN ? AND ?
        """,
        (user_id, prev_monday.isoformat(), prev_sunday.isoformat()),
    ).fetchone()
    penalty = row["total_overtime"] or 0

    return max(0, WEEKLY_LIMIT_MIN - penalty)


# ---------------------------------------------------------
# 登録・ログイン
# ---------------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        class_grade = request.form.get("class_grade", "").strip()
        attendance_no = request.form.get("attendance_no", "").strip()
        name = request.form.get("name", "").strip()
        admin_code = request.form.get("admin_code", "").strip()

        error = None
        if class_grade not in CLASS_GRADES:
            error = "クラス学年を正しく選択してください。"
        elif not attendance_no.isdigit():
            error = "出席番号は数値で入力してください。"
        elif not name:
            error = "名前を入力してください。"
        elif admin_code and admin_code != ADMIN_CODE:
            error = "管理者コードが正しくありません。"

        if error is None:
            db = get_db()
            existing = db.execute(
                "SELECT id FROM users WHERE class_grade = ? AND attendance_no = ?",
                (class_grade, int(attendance_no)),
            ).fetchone()
            if existing is not None:
                error = "同じクラス・出席番号のユーザーが既に登録されています。"

        if error is not None:
            flash(error, "error")
            return render_template("register.html", class_grades=CLASS_GRADES, form=request.form)

        is_admin = 1 if admin_code and admin_code == ADMIN_CODE else 0

        db = get_db()
        cur = db.execute(
            "INSERT INTO users (class_grade, attendance_no, name, is_admin) VALUES (?, ?, ?, ?)",
            (class_grade, int(attendance_no), name, is_admin),
        )
        db.commit()
        session["user_id"] = cur.lastrowid
        flash("登録が完了しました。", "success")
        return redirect(url_for("home"))

    return render_template("register.html", class_grades=CLASS_GRADES, form={})


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        class_grade = request.form.get("class_grade", "").strip()
        attendance_no = request.form.get("attendance_no", "").strip()

        error = None
        if class_grade not in CLASS_GRADES:
            error = "クラス学年を正しく選択してください。"
        elif not attendance_no.isdigit():
            error = "出席番号は数値で入力してください。"

        user = None
        if error is None:
            db = get_db()
            user = db.execute(
                "SELECT * FROM users WHERE class_grade = ? AND attendance_no = ?",
                (class_grade, int(attendance_no)),
            ).fetchone()
            if user is None:
                error = "該当するユーザーが見つかりません。登録から始めてください。"

        if error is not None:
            flash(error, "error")
            return render_template("login.html", class_grades=CLASS_GRADES, form=request.form)

        session["user_id"] = user["id"]
        flash(f"{user['name']}さん、ログインしました。", "success")
        return redirect(url_for("home"))

    return render_template("login.html", class_grades=CLASS_GRADES, form={})


@app.route("/logout")
def logout():
    session.clear()
    flash("ログアウトしました。", "success")
    return redirect(url_for("login"))


def build_month_calendar(year, month):
    """
    指定年月(月またぎのnavボタン対応で0以下・13以上も正規化)のカレンダーデータを組み立てる。
    home()と/calendarの両方から呼べるよう関数化している。
    """
    if month < 1:
        year -= 1
        month = 12
    elif month > 12:
        year += 1
        month = 1

    cal = calendar.Calendar(firstweekday=0)  # 月曜始まり
    month_days = cal.monthdatescalendar(year, month)

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)

    return year, month, month_days, prev_year, prev_month, next_year, next_month


# ---------------------------------------------------------
# ホーム
# ---------------------------------------------------------

@app.route("/")
@login_required
def home():
    db = get_db()
    user_id = session["user_id"]
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    today = date.today()
    now = datetime.now()
    now_date_str = now.strftime("%Y-%m-%d")
    now_time_str = now.strftime("%H:%M")

    # 自分の予約一覧(日付順、active/cancelled両方)
    reservations = db.execute(
        """
        SELECT * FROM reservations
        WHERE user_id = ?
        ORDER BY date ASC, start_time ASC
        """,
        (user_id,),
    ).fetchall()

    active_reservations = [r for r in reservations if r["status"] == "active"]
    today_reservation = next(
        (r for r in active_reservations if r["date"] == now_date_str), None
    )
    upcoming_reservations = [
        r for r in active_reservations if r["date"] > now_date_str
    ]

    # 今週の利用時間(予約ベース)と週上限(ペナルティ反映)
    weekly_used_min = get_weekly_reserved_minutes(user_id, today.isoformat())
    weekly_limit_min = get_weekly_limit_minutes(user_id, today.isoformat())
    weekly_remaining_min = max(0, weekly_limit_min - weekly_used_min)
    weekly_used_hours = round(weekly_used_min / 60, 1)
    weekly_limit_hours = round(weekly_limit_min / 60, 1)
    weekly_pct = (
        min(100, round(weekly_used_min / weekly_limit_min * 100, 1))
        if weekly_limit_min > 0
        else 100
    )

    # 入室中(checkout_atがNULL)のトレーニングログがあれば、退室画面への導線を出す
    active_log = db.execute(
        """
        SELECT training_logs.*, reservations.date AS r_date,
               reservations.start_time AS r_start_time, reservations.end_time AS r_end_time
        FROM training_logs
        JOIN reservations ON reservations.id = training_logs.reservation_id
        WHERE training_logs.user_id = ? AND training_logs.checkout_at IS NULL
        ORDER BY training_logs.id DESC LIMIT 1
        """,
        (user_id,),
    ).fetchone()

    # リマインダー通知用: 本日のactive予約の開始時刻一覧(JSへ渡すJSON)
    today_active_reservations = [
        r for r in active_reservations if r["date"] == now_date_str
    ]
    reminder_data = [
        {
            "reservation_id": r["id"],
            "start_time": f"{r['date']}T{r['start_time']}:00",
        }
        for r in today_active_reservations
    ]
    reminder_data_json = json.dumps(reminder_data)

    return render_template(
        "home.html",
        user=user,
        now_date_str=now_date_str,
        now_time_str=now_time_str,
        today_reservation=today_reservation,
        upcoming_reservations=upcoming_reservations,
        weekly_used_hours=weekly_used_hours,
        weekly_limit_hours=weekly_limit_hours,
        weekly_pct=weekly_pct,
        weekly_remaining_text=format_minutes(weekly_remaining_min),
        active_log=active_log,
        format_minutes=format_minutes,
        reminder_data_json=reminder_data_json,
    )


# ---------------------------------------------------------
# 予約タブ(月間カレンダー)
# ---------------------------------------------------------

@app.route("/calendar")
@login_required
def calendar_page():
    db = get_db()
    user_id = session["user_id"]

    today = date.today()
    year = request.args.get("year", type=int, default=today.year)
    month = request.args.get("month", type=int, default=today.month)

    year, month, month_days, prev_year, prev_month, next_year, next_month = (
        build_month_calendar(year, month)
    )

    reservations = db.execute(
        """
        SELECT * FROM reservations
        WHERE user_id = ?
        ORDER BY date ASC, start_time ASC
        """,
        (user_id,),
    ).fetchall()

    reserved_dates = {r["date"] for r in reservations if r["status"] == "active"}

    now = datetime.now()
    now_date_str = now.strftime("%Y-%m-%d")
    now_time_str = now.strftime("%H:%M")

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        month_days=month_days,
        today=today,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
        reserved_dates=reserved_dates,
        reservations=reservations,
        now_date_str=now_date_str,
        now_time_str=now_time_str,
    )


# ---------------------------------------------------------
# 予約
# ---------------------------------------------------------

@app.route("/reserve/<date_str>", methods=["GET", "POST"])
@login_required
def reserve(date_str):
    # 日付形式チェック
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("日付の形式が不正です。", "error")
        return redirect(url_for("home"))

    user_id = session["user_id"]
    db = get_db()

    if request.method == "POST":
        start_time = request.form.get("start_time", "")
        end_time = request.form.get("end_time", "")

        error = None
        if start_time not in TIME_SLOTS or end_time not in TIME_SLOTS:
            error = "時刻を正しく選択してください。"
        elif end_time <= start_time:
            error = "終了時刻は開始時刻より後にしてください。"
        else:
            now = datetime.now()
            start_dt = datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M")
            if start_dt < now:
                error = "過去の日時には予約できません。"

        new_duration = 0
        if error is None:
            s = datetime.strptime(start_time, "%H:%M")
            e = datetime.strptime(end_time, "%H:%M")
            new_duration = int((e - s).total_seconds() // 60)

            weekly_limit = get_weekly_limit_minutes(user_id, date_str)
            weekly_used = get_weekly_reserved_minutes(user_id, date_str)
            if weekly_used + new_duration > weekly_limit:
                remaining = max(0, weekly_limit - weekly_used)
                error = f"週7時間の予約上限を超えます。今週の残り予約可能時間は{format_minutes(remaining)}です。"

        if error is None:
            full_slot = find_full_slot(date_str, start_time, end_time)
            if full_slot is not None:
                capacity = get_room_capacity()
                error = f"{full_slot}の時間帯は満員（定員{capacity}名）のため予約できません。"

        if error is not None:
            flash(error, "error")
            return render_template(
                "reserve.html",
                date_str=date_str,
                target_date=target_date,
                time_slots=TIME_SLOTS,
                form=request.form,
            )

        db.execute(
            """
            INSERT INTO reservations (user_id, date, start_time, end_time, status, created_at)
            VALUES (?, ?, ?, ?, 'active', ?)
            """,
            (user_id, date_str, start_time, end_time, datetime.now().isoformat(timespec="seconds")),
        )
        db.commit()
        flash("予約しました。", "success")
        return redirect(url_for("home"))

    # 過去日は予約不可
    if target_date < date.today():
        flash("過去の日付には予約できません。", "error")
        return redirect(url_for("home"))

    weekly_limit = get_weekly_limit_minutes(user_id, date_str)
    weekly_used = get_weekly_reserved_minutes(user_id, date_str)
    remaining_min = max(0, weekly_limit - weekly_used)

    penalty_min = WEEKLY_LIMIT_MIN - weekly_limit
    has_penalty = penalty_min > 0

    return render_template(
        "reserve.html",
        date_str=date_str,
        target_date=target_date,
        time_slots=TIME_SLOTS,
        form={},
        remaining_text=format_minutes(remaining_min),
        has_penalty=has_penalty,
        penalty_text=format_minutes(penalty_min) if has_penalty else None,
        weekly_limit_text=format_minutes(weekly_limit) if has_penalty else None,
    )


@app.route("/reservation/<int:reservation_id>/cancel", methods=["POST"])
@login_required
def cancel_reservation(reservation_id):
    db = get_db()
    user_id = session["user_id"]
    reservation = db.execute(
        "SELECT * FROM reservations WHERE id = ? AND user_id = ?",
        (reservation_id, user_id),
    ).fetchone()
    if reservation is None:
        flash("予約が見つかりません。", "error")
        return redirect(url_for("home"))

    db.execute(
        "UPDATE reservations SET status = 'cancelled' WHERE id = ?",
        (reservation_id,),
    )
    db.commit()
    flash("予約を取り消しました。", "success")
    return redirect(url_for("home"))


# ---------------------------------------------------------
# 週間レポート
# ---------------------------------------------------------

WEEKDAY_LABELS_JA = ["月", "火", "水", "木", "金", "土", "日"]


@app.route("/report")
@login_required
def report():
    db = get_db()
    user_id = session["user_id"]
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())

    # (a) 今週(月〜日)の日別トレーニング時間(training_logsの実働分。退室済みのみ)
    week_logs = db.execute(
        """
        SELECT date, duration_min, overtime_min FROM training_logs
        WHERE user_id = ? AND date BETWEEN ? AND ? AND checkout_at IS NOT NULL
        """,
        (user_id, this_monday.isoformat(), (this_monday + timedelta(days=6)).isoformat()),
    ).fetchall()

    daily_normal = [0] * 7
    daily_overtime = [0] * 7
    for row in week_logs:
        d = datetime.strptime(row["date"], "%Y-%m-%d").date()
        idx = (d - this_monday).days
        if 0 <= idx < 7:
            duration = row["duration_min"] or 0
            overtime = row["overtime_min"] or 0
            normal = max(0, duration - overtime)
            daily_normal[idx] += normal
            daily_overtime[idx] += overtime

    daily_total = [daily_normal[i] + daily_overtime[i] for i in range(7)]
    daily_max = max(daily_total) if max(daily_total) > 0 else 1

    daily_bars = []
    for i in range(7):
        day = this_monday + timedelta(days=i)
        daily_bars.append(
            {
                "label": WEEKDAY_LABELS_JA[i],
                "date": day.isoformat(),
                "normal_min": daily_normal[i],
                "overtime_min": daily_overtime[i],
                "total_min": daily_total[i],
                "normal_pct": round(daily_normal[i] / daily_max * 100, 1),
                "overtime_pct": round(daily_overtime[i] / daily_max * 100, 1),
                "total_text": format_minutes(daily_total[i]),
            }
        )

    # (b) 直近4週間(今週含む)の週合計(training_logsの実働分)
    weekly_bars = []
    week_totals = []
    for w in range(3, -1, -1):
        week_monday = this_monday - timedelta(days=7 * w)
        week_sunday = week_monday + timedelta(days=6)
        row = db.execute(
            """
            SELECT COALESCE(SUM(duration_min), 0) AS total
            FROM training_logs
            WHERE user_id = ? AND date BETWEEN ? AND ? AND checkout_at IS NOT NULL
            """,
            (user_id, week_monday.isoformat(), week_sunday.isoformat()),
        ).fetchone()
        total_min = row["total"] or 0
        week_totals.append(
            {
                "label": f"{week_monday.month}/{week_monday.day}週",
                "total_min": total_min,
                "is_this_week": w == 0,
            }
        )

    weekly_max = max((w["total_min"] for w in week_totals), default=0)
    weekly_max = weekly_max if weekly_max > 0 else 1
    for w in week_totals:
        weekly_bars.append(
            {
                "label": w["label"],
                "total_min": w["total_min"],
                "total_text": format_minutes(w["total_min"]),
                "pct": round(w["total_min"] / weekly_max * 100, 1),
                "is_this_week": w["is_this_week"],
            }
        )

    weekly_limit = get_weekly_limit_minutes(user_id, today.isoformat())

    return render_template(
        "report.html",
        daily_bars=daily_bars,
        weekly_bars=weekly_bars,
        weekly_limit_text=format_minutes(weekly_limit),
    )


# ---------------------------------------------------------
# トレーニングセッション(入室・タイマー・退室)
# ---------------------------------------------------------

@app.route("/session/<int:reservation_id>/checkin", methods=["POST"])
@login_required
def checkin(reservation_id):
    db = get_db()
    user_id = session["user_id"]
    reservation = db.execute(
        "SELECT * FROM reservations WHERE id = ? AND user_id = ? AND status = 'active'",
        (reservation_id, user_id),
    ).fetchone()
    if reservation is None:
        flash("予約が見つかりません。", "error")
        return redirect(url_for("home"))

    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    now_time_str = now.strftime("%H:%M")

    if reservation["date"] != today_str:
        flash("当日以外は入室できません。", "error")
        return redirect(url_for("home"))
    if now_time_str < reservation["start_time"]:
        flash("開始時刻より前は入室できません。", "error")
        return redirect(url_for("home"))

    # 既存の入室中ログがあればそれを使い、無ければ新規作成
    log = db.execute(
        """
        SELECT * FROM training_logs
        WHERE reservation_id = ? AND checkout_at IS NULL
        """,
        (reservation_id,),
    ).fetchone()

    if log is None:
        db.execute(
            """
            INSERT INTO training_logs (user_id, reservation_id, date, checkin_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, reservation_id, today_str, now.isoformat(timespec="seconds")),
        )
        db.commit()

    return redirect(url_for("training_session", reservation_id=reservation_id))


@app.route("/session/<int:reservation_id>")
@login_required
def training_session(reservation_id):
    db = get_db()
    user_id = session["user_id"]
    reservation = db.execute(
        "SELECT * FROM reservations WHERE id = ? AND user_id = ?",
        (reservation_id, user_id),
    ).fetchone()
    if reservation is None:
        flash("予約が見つかりません。", "error")
        return redirect(url_for("home"))

    log = db.execute(
        """
        SELECT * FROM training_logs
        WHERE reservation_id = ? AND checkout_at IS NULL
        ORDER BY id DESC LIMIT 1
        """,
        (reservation_id,),
    ).fetchone()

    if log is None:
        flash("入室していません。ホームから入室してください。", "error")
        return redirect(url_for("home"))

    return render_template("session.html", reservation=reservation, log=log)


@app.route("/session/<int:reservation_id>/checkout", methods=["POST"])
@login_required
def checkout(reservation_id):
    db = get_db()
    user_id = session["user_id"]
    reservation = db.execute(
        "SELECT * FROM reservations WHERE id = ? AND user_id = ?",
        (reservation_id, user_id),
    ).fetchone()
    if reservation is None:
        flash("予約が見つかりません。", "error")
        return redirect(url_for("home"))

    log = db.execute(
        """
        SELECT * FROM training_logs
        WHERE reservation_id = ? AND checkout_at IS NULL
        ORDER BY id DESC LIMIT 1
        """,
        (reservation_id,),
    ).fetchone()
    if log is None:
        flash("入室記録が見つかりません。", "error")
        return redirect(url_for("home"))

    now = datetime.now()
    checkin_at = datetime.fromisoformat(log["checkin_at"])
    duration_min = int((now - checkin_at).total_seconds() // 60)

    scheduled_end = datetime.strptime(
        f"{reservation['date']} {reservation['end_time']}", "%Y-%m-%d %H:%M"
    )
    overtime_min = max(0, int((now - scheduled_end).total_seconds() // 60))

    db.execute(
        """
        UPDATE training_logs
        SET checkout_at = ?, duration_min = ?, overtime_min = ?
        WHERE id = ?
        """,
        (now.isoformat(timespec="seconds"), duration_min, overtime_min, log["id"]),
    )
    db.commit()

    flash(
        f"退室しました。トレーニング時間: {format_minutes(duration_min)}"
        + (f"(超過{format_minutes(overtime_min)})" if overtime_min else ""),
        "success",
    )
    return redirect(url_for("home"))


# ---------------------------------------------------------
# 管理者ダッシュボード
# ---------------------------------------------------------

@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    today_str = date.today().isoformat()

    # (a) 本日の全予約一覧(全ユーザー分、時間順)
    today_reservations = db.execute(
        """
        SELECT reservations.*, users.name AS user_name, users.class_grade AS user_class_grade,
               users.attendance_no AS user_attendance_no
        FROM reservations
        JOIN users ON users.id = reservations.user_id
        WHERE reservations.date = ?
        ORDER BY reservations.start_time ASC
        """,
        (today_str,),
    ).fetchall()

    # (b) ユーザー一覧(今週の予約時間合計を付与)
    all_users = db.execute(
        "SELECT * FROM users ORDER BY class_grade ASC, attendance_no ASC"
    ).fetchall()
    users_with_stats = []
    for u in all_users:
        weekly_min = get_weekly_reserved_minutes(u["id"], today_str)
        users_with_stats.append({"user": u, "weekly_min": weekly_min})

    # (c) トレーニング実績(training_logsの全件、新しい順)
    training_logs = db.execute(
        """
        SELECT training_logs.*, users.name AS user_name, users.class_grade AS user_class_grade,
               users.attendance_no AS user_attendance_no
        FROM training_logs
        JOIN users ON users.id = training_logs.user_id
        ORDER BY training_logs.id DESC
        """
    ).fetchall()

    return render_template(
        "admin.html",
        today_reservations=today_reservations,
        users_with_stats=users_with_stats,
        training_logs=training_logs,
        format_minutes=format_minutes,
        room_capacity=get_room_capacity(),
    )


@app.route("/admin/settings", methods=["POST"])
@admin_required
def admin_settings():
    db = get_db()
    room_capacity = request.form.get("room_capacity", "").strip()

    error = None
    if not room_capacity.isdigit() or int(room_capacity) < 1:
        error = "定員は1以上の整数で入力してください。"

    if error is not None:
        flash(error, "error")
        return redirect(url_for("admin_dashboard") + "#admin-settings")

    db.execute(
        "INSERT INTO settings (key, value) VALUES ('room_capacity', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (room_capacity,),
    )
    db.commit()
    flash(f"定員を{room_capacity}名に変更しました。", "success")
    return redirect(url_for("admin_dashboard") + "#admin-settings")


@app.route("/admin/reservation/<int:reservation_id>/cancel", methods=["POST"])
@admin_required
def admin_cancel_reservation(reservation_id):
    db = get_db()
    reservation = db.execute(
        "SELECT * FROM reservations WHERE id = ?",
        (reservation_id,),
    ).fetchone()
    if reservation is None:
        flash("予約が見つかりません。", "error")
        return redirect(url_for("admin_dashboard"))

    db.execute(
        "UPDATE reservations SET status = 'cancelled' WHERE id = ?",
        (reservation_id,),
    )
    db.commit()
    flash("予約を強制的に取り消しました。", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/user/<int:user_id>/toggle_admin", methods=["POST"])
@admin_required
def admin_toggle_admin(user_id):
    if user_id == session.get("user_id"):
        flash("自分自身の管理者権限は変更できません。", "error")
        return redirect(url_for("admin_dashboard"))

    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if target is None:
        flash("ユーザーが見つかりません。", "error")
        return redirect(url_for("admin_dashboard"))

    new_value = 0 if target["is_admin"] else 1
    db.execute("UPDATE users SET is_admin = ? WHERE id = ?", (new_value, user_id))
    db.commit()
    flash(
        f"{target['name']}さんの管理者権限を{'付与' if new_value else '剥奪'}しました。",
        "success",
    )
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/export.csv")
@admin_required
def export_csv():
    db = get_db()
    rows = db.execute(
        """
        SELECT training_logs.date AS log_date, users.class_grade AS user_class_grade,
               users.attendance_no AS user_attendance_no, users.name AS user_name,
               training_logs.checkin_at, training_logs.checkout_at,
               training_logs.duration_min, training_logs.overtime_min
        FROM training_logs
        JOIN users ON users.id = training_logs.user_id
        ORDER BY training_logs.id DESC
        """
    ).fetchall()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["日付", "クラス", "出席番号", "名前", "入室時刻", "退室時刻", "実施時間(分)", "超過時間(分)"])
    for r in rows:
        writer.writerow(
            [
                r["log_date"],
                r["user_class_grade"],
                r["user_attendance_no"],
                r["user_name"],
                r["checkin_at"] or "",
                r["checkout_at"] or "",
                r["duration_min"] if r["duration_min"] is not None else "",
                r["overtime_min"] if r["overtime_min"] is not None else "",
            ]
        )

    csv_bytes = buffer.getvalue().encode("utf-8-sig")
    response = Response(csv_bytes, mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=training_logs.csv"
    return response


if __name__ == "__main__":
    database.init_db()
    app.run(debug=True)

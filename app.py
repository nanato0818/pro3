"""
トレーニングルーム予約システム
Flaskアプリ本体。ルート定義とDB初期化を行う。
"""
import calendar
from datetime import datetime, date, timedelta

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify

import database
from database import get_db

app = Flask(__name__)
app.secret_key = "training-room-app-secret-key-phase1"  # フェーズ1用の簡易シークレットキー

database.init_app(app)

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
# 運用時は変更すること
ADMIN_CODE = "pro3-admin-2026"


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


# ---------------------------------------------------------
# ホーム(カレンダー・予約一覧)
# ---------------------------------------------------------

@app.route("/")
@login_required
def home():
    db = get_db()
    user_id = session["user_id"]
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    # 表示対象の年月(クエリパラメータ。無指定なら今月)
    today = date.today()
    year = request.args.get("year", type=int, default=today.year)
    month = request.args.get("month", type=int, default=today.month)

    # 月またぎのnavボタン対応(0以下・13以上を正規化)
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

    # 自分の予約一覧(日付順、active/cancelled両方。今後の一覧表示用)
    reservations = db.execute(
        """
        SELECT * FROM reservations
        WHERE user_id = ?
        ORDER BY date ASC, start_time ASC
        """,
        (user_id,),
    ).fetchall()

    # 当日開始済みで入室可能な予約を判定するための現在時刻
    now = datetime.now()
    now_date_str = now.strftime("%Y-%m-%d")
    now_time_str = now.strftime("%H:%M")

    # 本日のトレーニング実績(training_logsから)
    today_logs = db.execute(
        """
        SELECT * FROM training_logs
        WHERE user_id = ? AND date = ? AND checkout_at IS NOT NULL
        """,
        (user_id, now_date_str),
    ).fetchall()
    today_total_min = sum(row["duration_min"] or 0 for row in today_logs)

    return render_template(
        "home.html",
        user=user,
        year=year,
        month=month,
        month_days=month_days,
        today=today,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
        reservations=reservations,
        now_date_str=now_date_str,
        now_time_str=now_time_str,
        today_total_min=today_total_min,
        format_minutes=format_minutes,
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

            weekly_used = get_weekly_reserved_minutes(user_id, date_str)
            if weekly_used + new_duration > WEEKLY_LIMIT_MIN:
                remaining = max(0, WEEKLY_LIMIT_MIN - weekly_used)
                error = f"週7時間の予約上限を超えます。今週の残り予約可能時間は{format_minutes(remaining)}です。"

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

    weekly_used = get_weekly_reserved_minutes(user_id, date_str)
    remaining_min = max(0, WEEKLY_LIMIT_MIN - weekly_used)

    return render_template(
        "reserve.html",
        date_str=date_str,
        target_date=target_date,
        time_slots=TIME_SLOTS,
        form={},
        remaining_text=format_minutes(remaining_min),
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
    )


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


if __name__ == "__main__":
    database.init_db()
    app.run(debug=True)

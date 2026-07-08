/* トレーニングルーム予約システム - vanilla JS
   予約取り消し確認ダイアログ / トレーニングセッションのタイマー処理 */

document.addEventListener("DOMContentLoaded", function () {
    // 予約取り消しの確認ダイアログ
    document.querySelectorAll(".js-confirm-cancel").forEach(function (form) {
        form.addEventListener("submit", function (e) {
            if (!confirm("この予約を取り消しますか？")) {
                e.preventDefault();
            }
        });
    });

    // トレーニングセッション画面のタイマー処理
    const root = document.getElementById("session-root");
    if (root) {
        initSessionTimer(root);
    }

    // ホーム画面の予約リマインダー通知(session.htmlの超過通知とは独立したロジック)
    const reminderDataEl = document.getElementById("reminder-data");
    if (reminderDataEl) {
        initHomeReminders(reminderDataEl);
    }

    // ホーム画面: 本日の予約カードのライブ更新(開始前/利用可能/終了後の切り替え)
    const todayCardActions = document.getElementById("today-card-actions");
    if (todayCardActions) {
        initTodayCard(todayCardActions);
    }
});

function initSessionTimer(root) {
    const checkinAt = new Date(root.dataset.checkinAt.replace(" ", "T"));
    const scheduledEnd = new Date(root.dataset.scheduledEnd);

    const elapsedEl = document.getElementById("elapsed-time");
    const remainingEl = document.getElementById("remaining-time");
    const remainingLabelEl = document.getElementById("remaining-label");
    const overtimeWarningEl = document.getElementById("overtime-warning");
    const panelEl = document.getElementById("session-panel");

    // ブラウザ通知の許可をリクエスト(ページ表示時)
    let notified = false;
    if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission();
    }

    function formatHMS(totalSeconds) {
        const sign = totalSeconds < 0 ? "-" : "";
        const abs = Math.abs(Math.floor(totalSeconds));
        const h = Math.floor(abs / 3600);
        const m = Math.floor((abs % 3600) / 60);
        const s = abs % 60;
        const pad = (n) => String(n).padStart(2, "0");
        return `${sign}${pad(h)}:${pad(m)}:${pad(s)}`;
    }

    function tick() {
        const now = new Date();

        const elapsedSec = (now - checkinAt) / 1000;
        elapsedEl.textContent = formatHMS(elapsedSec);

        const remainingSec = (scheduledEnd - now) / 1000;

        if (remainingSec >= 0) {
            remainingEl.textContent = formatHMS(remainingSec);
            remainingLabelEl.textContent = "終了予定までの残り時間";
            overtimeWarningEl.hidden = true;
            panelEl.classList.remove("is-overtime");
        } else {
            // 超過中: 経過した超過分を表示し、警告色に切り替える(超過量なので正の値で表示)
            remainingEl.textContent = formatHMS(Math.abs(remainingSec));
            remainingLabelEl.textContent = "超過時間";
            overtimeWarningEl.hidden = false;
            panelEl.classList.add("is-overtime");

            if (!notified && "Notification" in window && Notification.permission === "granted") {
                new Notification("予約時間を超過しています");
                notified = true;
            }
        }
    }

    tick();
    setInterval(tick, 1000);
}

// ホーム画面専用: 開始15分前の予約リマインダー通知
// (session.htmlの超過通知ロジックとは名前空間・関数を分離している)
const HOME_REMINDER_STORAGE_KEY = "homeReminderNotifiedIds";

function initHomeReminders(reminderDataEl) {
    let reminders;
    try {
        reminders = JSON.parse(reminderDataEl.textContent || "[]");
    } catch (err) {
        reminders = [];
    }

    if (!reminders.length) {
        return;
    }

    if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission();
    }

    function getNotifiedIds() {
        try {
            const raw = sessionStorage.getItem(HOME_REMINDER_STORAGE_KEY);
            return raw ? JSON.parse(raw) : [];
        } catch (err) {
            return [];
        }
    }

    function markNotified(reservationId) {
        const ids = getNotifiedIds();
        if (!ids.includes(reservationId)) {
            ids.push(reservationId);
            sessionStorage.setItem(HOME_REMINDER_STORAGE_KEY, JSON.stringify(ids));
        }
    }

    function checkReminders() {
        if (!("Notification" in window) || Notification.permission !== "granted") {
            return;
        }
        const now = new Date();
        const notifiedIds = getNotifiedIds();

        reminders.forEach(function (r) {
            if (notifiedIds.includes(r.reservation_id)) {
                return;
            }
            const startAt = new Date(r.start_time);
            const diffMin = (startAt - now) / 60000;

            if (diffMin > 0 && diffMin <= 15) {
                const hh = String(startAt.getHours()).padStart(2, "0");
                const mm = String(startAt.getMinutes()).padStart(2, "0");
                new Notification(`まもなく予約時間です（${hh}:${mm}開始）`);
                markNotified(r.reservation_id);
            }
        });
    }

    checkReminders();
    setInterval(checkReminders, 30000);
}

// ホーム画面専用: 本日の予約カードの「入室する」ボタンをページを開きっぱなしでも
// 追随させるライブ更新処理(既存のリマインダー通知ロジックとは独立)
function initTodayCard(container) {
    const startAt = new Date(container.dataset.startAt.replace(" ", "T"));
    const endAt = new Date(container.dataset.endAt.replace(" ", "T"));
    const endLabel = container.dataset.endLabel;
    const checkinUrl = container.dataset.checkinUrl;
    const checkedOut = container.dataset.checkedOut === "1";

    const slotEl = document.getElementById("today-checkin-slot");
    const statusEl = document.getElementById("today-card-status");
    if (!slotEl || !statusEl) {
        return;
    }

    const checkinLabel = checkedOut ? "再入室" : "入室する";

    function render() {
        const now = new Date();

        if (now < startAt) {
            const diffMin = Math.max(1, Math.ceil((startAt - now) / 60000));
            slotEl.innerHTML =
                '<button type="button" class="btn btn-primary" disabled>入室する（開始前）</button>';
            statusEl.textContent = `開始まであと${diffMin}分`;
        } else if (now < endAt) {
            slotEl.innerHTML =
                '<form method="post" action="' + checkinUrl + '" class="inline-form-block">' +
                '<button type="submit" class="btn btn-primary">' + checkinLabel + "</button>" +
                "</form>";
            statusEl.textContent = `利用可能な時間帯です（〜${endLabel}）`;
        } else {
            slotEl.innerHTML = "";
            statusEl.textContent = "本日の予約時間は終了しました";
        }
    }

    render();
    setInterval(render, 30000);
}

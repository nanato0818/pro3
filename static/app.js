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

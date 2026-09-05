const statusLabel = {
  running: "运行中",
  stopping: "正在停止",
  stopped: "已停止",
  success: "成功",
  finished_with_errors: "有错误",
  failed: "失败",
  skipped: "已跳过",
  info: "信息"
};

const statusBadge = {
  running: "text-bg-primary",
  stopping: "text-bg-warning",
  stopped: "text-bg-secondary",
  success: "text-bg-success",
  finished_with_errors: "text-bg-danger",
  failed: "text-bg-danger",
  skipped: "text-bg-secondary"
};

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function badge(status) {
  const cls = statusBadge[status] || "text-bg-secondary";
  return `<span class="badge rounded-pill ${cls}">${escapeHtml(statusLabel[status] || status || "")}</span>`;
}

async function refreshDetail() {
  if (!window.RUN_LIVE_URL) return;
  try {
    const response = await fetch(window.RUN_LIVE_URL, { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    const counts = data.counts || {};
    const statusBox = document.querySelector("#detail-status");
    const total = document.querySelector("#detail-total");
    const success = document.querySelector("#detail-success");
    const failed = document.querySelector("#detail-failed");
    const message = document.querySelector("#detail-message");
    const logBox = document.querySelector("#run-log");
    if (statusBox) statusBox.innerHTML = badge(data.status);
    if (total) total.textContent = counts.total || 0;
    if (success) success.textContent = counts.success || 0;
    if (failed) failed.textContent = counts.failed || 0;
    if (message) message.textContent = data.message || "";
    if (logBox) {
      const logs = data.logs || [];
      const nearBottom = logBox.scrollHeight - logBox.scrollTop - logBox.clientHeight < 60;
      logBox.innerHTML = logs.map((item) => `<div class="run-log-line is-${escapeHtml(item.status || "info")}">
        <span class="run-log-time">${escapeHtml(item.time || "")}</span>
        <span class="run-log-status">${badge(item.status)}</span>
        <span class="run-log-msg">${escapeHtml(item.message || "")}</span>
      </div>`).join("") || '<div class="text-secondary small px-2 py-3">等待抓取日志...</div>';
      if (nearBottom) logBox.scrollTop = logBox.scrollHeight;
    }
    if (!["running", "stopping"].includes(data.status) && window._runLiveTimer) {
      clearInterval(window._runLiveTimer);
    }
  } catch (error) {
    // keep last snapshot
  }
}

refreshDetail();
window._runLiveTimer = setInterval(refreshDetail, 2000);

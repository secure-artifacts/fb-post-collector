const statusLabel = {
  running: "运行中",
  paused: "已暂停",
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
  paused: "text-bg-info",
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
  const label = statusLabel[status] || status || "";
  return `<span class="badge rounded-pill ${cls}">${escapeHtml(label)}</span>`;
}

function renderLog(logs) {
  const box = document.querySelector("#run-log");
  if (!box) return;
  if (!logs || !logs.length) {
    box.innerHTML = '<div class="text-secondary small px-2 py-3">运行开始后，这里会实时显示正在抓取的行、成功/失败和失败原因。</div>';
    return;
  }
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  box.innerHTML = logs.map((item) => {
    const status = item.status || "info";
    return `<div class="run-log-line is-${escapeHtml(status)}">
      <span class="run-log-time">${escapeHtml(item.time || "")}</span>
      <span class="run-log-status">${badge(status)}</span>
      <span class="run-log-msg">${escapeHtml(item.message || "")}</span>
    </div>`;
  }).join("");
  if (nearBottom) box.scrollTop = box.scrollHeight;
}

function renderSummary(counts) {
  const data = counts || {};
  document.querySelectorAll("#live-summary [data-k]").forEach((el) => {
    el.textContent = data[el.dataset.k] || 0;
  });
}

function renderActive(active) {
  const title = document.querySelector("#live-run-title");
  const current = document.querySelector("#live-current");
  const stopBox = document.querySelector("#live-run-stop");
  if (!active || active.error) {
    title.textContent = "还没有正在运行的任务。";
    current.textContent = "等待任务开始...";
    stopBox.innerHTML = "";
    renderSummary({});
    renderLog([]);
    return;
  }
  const live = ["running", "paused", "stopping"].includes(active.status);
  title.textContent = live
    ? `正在运行 #${active.id} ${active.project_name || ""}`.trim()
    : `最近一次 #${active.id} ${active.project_name || ""}`.trim();
  if (active.current_row) {
    current.textContent = `当前：第 ${active.current_row} 行 ${active.current_url || ""}`.trim();
  } else {
    current.textContent = active.message || (live ? "正在准备..." : "本次运行已结束");
  }
  if (live) {
    const stopping = active.status === "stopping";
    const pauseControl = active.status === "running"
      ? `<form action="${window.RUNS_BASE}/${active.id}/pause" method="post" class="inline"><button class="btn btn-info btn-sm" type="submit">暂停</button></form>`
      : active.status === "paused"
        ? `<form action="${window.RUNS_BASE}/${active.id}/resume" method="post" class="inline"><button class="btn btn-success btn-sm" type="submit">继续</button></form>`
        : "";
    stopBox.innerHTML = `${pauseControl} <form action="${window.RUNS_BASE}/${active.id}/stop" method="post" class="inline">
      <button class="btn btn-warning btn-sm" type="submit" ${stopping ? "disabled" : ""}>停止</button>
    </form>`;
  } else {
    stopBox.innerHTML = "";
  }
  renderSummary(active.counts);
  renderLog(active.logs);
}

function renderRuns(runs) {
  const body = document.querySelector("#runs-body");
  if (!body) return;
  if (!runs.length) {
    body.innerHTML = '<tr><td colspan="10" class="text-center text-secondary py-4">还没有运行记录</td></tr>';
    return;
  }
  body.innerHTML = runs.map((run) => {
    const pauseControl = run.status === "running"
      ? `<form action="${window.RUNS_BASE}/${run.id}/pause" method="post" class="inline"><button class="btn btn-info btn-sm" type="submit">暂停</button></form>`
      : run.status === "paused"
        ? `<form action="${window.RUNS_BASE}/${run.id}/resume" method="post" class="inline"><button class="btn btn-success btn-sm" type="submit">继续</button></form>`
        : "";
    const stop = ["running", "paused", "stopping"].includes(run.status)
      ? `${pauseControl} <form action="${window.RUNS_BASE}/${run.id}/stop" method="post" class="inline">
           <button class="btn btn-warning btn-sm" type="submit" ${run.status === "stopping" ? "disabled" : ""}>停止</button>
         </form>`
      : "";
    return `<tr data-run-id="${run.id}">
      <td><a href="${window.RUNS_BASE}/${run.id}">${run.id}</a></td>
      <td>${escapeHtml(run.project_name)}</td>
      <td>${escapeHtml(run.task_name || "手动")}</td>
      <td>${badge(run.status)}</td>
      <td>${run.total_rows || 0}</td>
      <td>${run.success_rows || 0}</td>
      <td>${run.failed_rows || 0}</td>
      <td>${run.skipped_rows || 0}</td>
      <td>${escapeHtml(run.error_message)}</td>
      <td class="text-center">${stop}</td>
    </tr>`;
  }).join("");
}

async function refreshLive() {
  if (!window.RUNS_LIVE_URL) return;
  try {
    const response = await fetch(window.RUNS_LIVE_URL, { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    renderActive(data.active);
    renderRuns(data.runs || []);
  } catch (error) {
    // keep last snapshot
  }
}

refreshLive();
setInterval(refreshLive, 2000);

const list = document.querySelector("#fields-list");
const form = document.querySelector("#project-form");
const projectType = document.querySelector("#project-type");
const fieldConfigMessage = document.querySelector("#field-config-message");
let dragged = null;

const recommendedFields = [
  ["post_id", "贴文ID"],
  ["post_text", "贴文信息"],
  ["post_text_zh", "贴文信息翻译"],
  ["post_url", "贴文链接"],
  ["original_post_url", "贴文原始链接"],
  ["post_time", "贴文时间"],
  ["media_preview_formula", "贴文多媒体"],
  ["like_count", "贴文点赞量"],
  ["comment_count", "贴文评论量"],
  ["share_count", "贴文分享量"],
  ["post_type", "贴文类型"],
  ["repost_or_original", "转贴/原创"],
  ["page_id", "专页ID-链接里的ID"],
  ["ocr_text", "贴文OCR"],
  ["ocr_text_zh", "贴文OCR文本翻译"],
  ["is_edited", "是否为改贴"],
  ["media_url", "图片链接"],
  ["audio_text", "音频文本"],
  ["audio_text_zh", "文本翻译"]
];

function syncProjectMode() {
  const mode = projectType ? projectType.value : "post";
  document.querySelectorAll(".project-mode").forEach((section) => {
    section.classList.toggle("d-none", !section.classList.contains(`project-mode-${mode}`));
  });
  const postUrlRow = document.querySelector('.field-row[data-key="post_url"]');
  if (postUrlRow && mode === "page") {
    postUrlRow.querySelector("input[type='checkbox']").checked = true;
  }
}

function serializeFields() {
  return [...document.querySelectorAll(".field-row")].map((row) => ({
    field_key: row.dataset.key,
    enabled: row.querySelector("input[type='checkbox']").checked,
    field_label: row.querySelector(".field-label").value,
    write_column: normalizeColumn(row.querySelector(".field-column")?.value)
  }));
}

function normalizeColumn(value) {
  return String(value || "").trim().toUpperCase().replace(/[^A-Z]/g, "").slice(0, 3);
}

function columnToIndex(col) {
  let total = 0;
  for (const ch of normalizeColumn(col)) {
    total = total * 26 + (ch.charCodeAt(0) - 64);
  }
  return total;
}

function indexToColumn(index) {
  let result = "";
  let n = Number(index);
  while (n > 0) {
    const rem = (n - 1) % 26;
    result = String.fromCharCode(65 + rem) + result;
    n = Math.floor((n - 1) / 26);
  }
  return result;
}

function writeStartColumn() {
  return normalizeColumn(document.querySelector("#write-start-column")?.value) || "B";
}

function refreshFieldColumns(force = false) {
  if (!list) return;
  let offset = 0;
  const start = columnToIndex(writeStartColumn()) || 2;
  list.querySelectorAll(".field-row").forEach((row) => {
    const enabled = row.querySelector("input[type='checkbox']").checked;
    const input = row.querySelector(".field-column");
    if (!input) return;
    if (enabled) {
      if (force || !normalizeColumn(input.value)) {
        input.value = indexToColumn(start + offset);
      } else {
        input.value = normalizeColumn(input.value);
      }
      offset += 1;
    }
  });
  warnDuplicateColumns();
}

function warnDuplicateColumns() {
  const enabledColumns = serializeFields()
    .filter((field) => field.enabled && field.write_column)
    .map((field) => field.write_column);
  const duplicates = new Set(enabledColumns.filter((column, index) => enabledColumns.indexOf(column) !== index));
  list?.querySelectorAll(".field-row").forEach((row) => {
    const input = row.querySelector(".field-column");
    const enabled = row.querySelector("input[type='checkbox']").checked;
    const column = normalizeColumn(input?.value);
    input?.classList.toggle("is-duplicate", Boolean(enabled && column && duplicates.has(column)));
  });
  if (duplicates.size) {
    showFieldMessage(`有重复写入列：${[...duplicates].join("、")}。请改成不同列号后再保存。`, "warning");
  }
}

function showFieldMessage(message, type = "success") {
  if (!fieldConfigMessage) return;
  fieldConfigMessage.textContent = message;
  fieldConfigMessage.className = `alert alert-${type} py-2 small`;
}

if (projectType) {
  projectType.addEventListener("change", syncProjectMode);
  syncProjectMode();
}

if (list) {
  list.addEventListener("dragstart", (event) => {
    dragged = event.target.closest(".field-row");
    if (dragged) dragged.classList.add("dragging");
  });
  list.addEventListener("dragend", () => {
    if (dragged) dragged.classList.remove("dragging");
    dragged = null;
  });
  list.addEventListener("dragover", (event) => {
    event.preventDefault();
    const row = event.target.closest(".field-row");
    if (!row || row === dragged) return;
    const rect = row.getBoundingClientRect();
    const after = event.clientY > rect.top + rect.height / 2;
    list.insertBefore(dragged, after ? row.nextSibling : row);
  });

  list.addEventListener("click", (event) => {
    const row = event.target.closest(".field-row");
    if (!row) return;
    if (event.target.closest(".move-up") && row.previousElementSibling) {
      list.insertBefore(row, row.previousElementSibling);
    }
    if (event.target.closest(".move-down") && row.nextElementSibling) {
      list.insertBefore(row.nextElementSibling, row);
    }
  });
  list.addEventListener("change", (event) => {
    if (event.target.matches("input[type='checkbox']")) refreshFieldColumns(false);
    if (event.target.matches(".field-column")) {
      event.target.value = normalizeColumn(event.target.value);
      warnDuplicateColumns();
    }
  });
}

document.querySelector("#write-start-column")?.addEventListener("change", () => refreshFieldColumns(true));
document.querySelector("#renumber-field-columns")?.addEventListener("click", () => {
  refreshFieldColumns(true);
  showFieldMessage(`已按写入开始列 ${writeStartColumn()} 重新连号。请保存项目后生效。`, "success");
});

document.querySelector("#apply-recommended-fields")?.addEventListener("click", () => {
  const rows = new Map([...list.querySelectorAll(".field-row")].map((row) => [row.dataset.key, row]));
  const enabledKeys = new Set(recommendedFields.map(([key]) => key));
  recommendedFields.forEach(([key, label]) => {
    const row = rows.get(key);
    if (!row) return;
    row.querySelector("input[type='checkbox']").checked = true;
    row.querySelector(".field-label").value = label;
    list.appendChild(row);
  });
  rows.forEach((row, key) => {
    if (!enabledKeys.has(key)) {
      row.querySelector("input[type='checkbox']").checked = false;
      list.appendChild(row);
    }
  });
  refreshFieldColumns(true);
});

document.querySelector("#enable-all-fields")?.addEventListener("click", () => {
  list.querySelectorAll("input[type='checkbox']").forEach((input) => { input.checked = true; });
  refreshFieldColumns(false);
});

document.querySelector("#disable-all-fields")?.addEventListener("click", () => {
  list.querySelectorAll("input[type='checkbox']").forEach((input) => { input.checked = false; });
  if (projectType?.value === "page") {
    list.querySelector('.field-row[data-key="post_url"] input[type="checkbox"]').checked = true;
  }
  refreshFieldColumns(false);
});

document.querySelector("#export-fields")?.addEventListener("click", () => {
  const payload = {
    format: "fb-post-collector-fields",
    version: 2,
    exported_at: new Date().toISOString(),
    write_start_column: writeStartColumn(),
    fields: serializeFields()
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `FB字段配置-${new Date().toISOString().slice(0, 10)}.json`;
  link.click();
  URL.revokeObjectURL(url);
  showFieldMessage("字段配置已导出。", "success");
});

const importFieldsFile = document.querySelector("#import-fields-file");
document.querySelector("#import-fields")?.addEventListener("click", () => importFieldsFile?.click());
importFieldsFile?.addEventListener("change", async () => {
  const file = importFieldsFile.files?.[0];
  if (!file) return;
  try {
    const payload = JSON.parse(await file.text());
    const importedFields = Array.isArray(payload) ? payload : payload.fields;
    if (!Array.isArray(importedFields) || !importedFields.length) {
      throw new Error("文件中没有有效字段配置");
    }
    const rows = new Map([...list.querySelectorAll(".field-row")].map((row) => [row.dataset.key, row]));
    const importedKeys = new Set();
    importedFields.forEach((field) => {
      const key = String(field?.field_key || "");
      const row = rows.get(key);
      if (!row || importedKeys.has(key)) return;
      importedKeys.add(key);
      row.querySelector("input[type='checkbox']").checked = Boolean(field.enabled);
      if (typeof field.field_label === "string" && field.field_label.trim()) {
        row.querySelector(".field-label").value = field.field_label.trim();
      }
      const columnInput = row.querySelector(".field-column");
      if (columnInput && typeof field.write_column === "string") {
        columnInput.value = normalizeColumn(field.write_column);
      }
      list.appendChild(row);
    });
    rows.forEach((row, key) => {
      if (!importedKeys.has(key)) list.appendChild(row);
    });
    if (payload.write_start_column) {
      const startInput = document.querySelector("#write-start-column");
      if (startInput) startInput.value = normalizeColumn(payload.write_start_column) || startInput.value;
    }
    refreshFieldColumns(false);
    syncProjectMode();
    showFieldMessage(`成功导入 ${importedKeys.size} 个字段，请点击“保存项目”生效。`, "success");
  } catch (error) {
    showFieldMessage(`导入失败：${error.message || "文件格式不正确"}`, "danger");
  } finally {
    importFieldsFile.value = "";
  }
});

if (form) {
  form.addEventListener("submit", () => {
    refreshFieldColumns(false);
    document.querySelector("#fields-json").value = JSON.stringify(serializeFields());
  });
}

refreshFieldColumns(false);

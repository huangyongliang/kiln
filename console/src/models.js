/**
 * Save rules for the model catalog page.
 * Display names are checked here before PUT. A 422 is mapped back to the
 * row when the message and the current rows identify it.
 */

export const ROLE_ORDER = ["reader", "builder", "publisher", "admin"];
export const DISPLAY_NAME_MAX = 128;
export const CATALOG_MAX = 32;

export const NAME_TOO_LONG = "显示名不能超过 128 个字符。";
export const ID_TOO_LONG = "模型 id 不能超过 128 个字符，不会截断。";
export const ID_DUPLICATE = "模型 id 重复。";
export const TOO_MANY = "模型目录不能超过 32 项，不会只保留前 32 项。";
export const SAVED_TEXT = "已保存";
export const SAVE_FAILED_TAG = "保存失败";
export const UNSAVED_TAG = "尚未保存";

export function displayNameError(value) {
  const text = String(value ?? "").trim();
  if (text.length > DISPLAY_NAME_MAX) {
    return NAME_TOO_LONG;
  }
  return "";
}

export function catalogStatusTag(source, saveRejected) {
  if (saveRejected) {
    return SAVE_FAILED_TAG;
  }
  return source === "db" ? SAVED_TEXT : UNSAVED_TAG;
}

export function prepareCatalogSave({ rows, globalDefault, assignments, roleDefaults }) {
  const list = Array.isArray(rows) ? rows : [];
  const nameErrors = [];
  list.forEach((row, index) => {
    const message = displayNameError(row?.displayName);
    if (message) {
      nameErrors.push({ index, id: rowLabel(row, index), message });
    }
  });
  if (nameErrors.length) {
    return {
      ok: false,
      rowErrors: indexMap(nameErrors),
      message: rowMessage(nameErrors, NAME_TOO_LONG),
      body: null,
    };
  }
  if (list.length > CATALOG_MAX) {
    return { ok: false, rowErrors: {}, message: TOO_MANY, body: null };
  }
  const seen = new Set();
  for (const row of list) {
    const id = String(row?.id ?? "").trim();
    if (seen.has(id)) {
      return { ok: false, rowErrors: {}, message: ID_DUPLICATE, body: null };
    }
    seen.add(id);
  }
  return {
    ok: true,
    rowErrors: {},
    message: "",
    body: {
      models: list.map((row) => ({
        id: row.id,
        display_name: String(row.displayName ?? "").trim() || null,
      })),
      default_model: globalDefault || null,
      assignments: ROLE_ORDER.map((roleId) => ({
        role_id: roleId,
        models: [...(assignments?.[roleId] || [])],
        default_model: roleDefaults?.[roleId] || null,
      })),
    },
  };
}

export function mapSaveFailure(detail, rows) {
  const text = typeof detail === "string" && detail ? detail : "无法保存模型配置";
  const matches = rowsForDetail(text, rows);
  return {
    message: matches.length ? rowMessage(matches, text) : text,
    rowErrors: indexMap(matches),
    rejected: true,
  };
}

function rowsForDetail(detail, rows) {
  const list = Array.isArray(rows) ? rows : [];
  if (detail === NAME_TOO_LONG) {
    return list.flatMap((row, index) =>
      displayNameError(row?.displayName) ? [{ index, id: rowLabel(row, index), message: detail }] : [],
    );
  }
  if (detail === ID_TOO_LONG) {
    return list.flatMap((row, index) =>
      String(row?.id ?? "").trim().length > DISPLAY_NAME_MAX
        ? [{ index, id: rowLabel(row, index), message: detail }]
        : [],
    );
  }
  if (detail === ID_DUPLICATE) {
    const counts = new Map();
    for (const row of list) {
      const id = String(row?.id ?? "").trim();
      counts.set(id, (counts.get(id) || 0) + 1);
    }
    return list.flatMap((row, index) =>
      counts.get(String(row?.id ?? "").trim()) > 1
        ? [{ index, id: rowLabel(row, index), message: detail }]
        : [],
    );
  }
  return [];
}

function rowLabel(row, index) {
  const id = String(row?.id ?? "").trim();
  return id || `第 ${index + 1} 项`;
}

function rowMessage(matches, detail) {
  const labels = [];
  for (const item of matches) {
    if (!labels.includes(item.id)) {
      labels.push(item.id);
    }
  }
  return `${labels.join("、")}：${detail}`;
}

function indexMap(matches) {
  const errors = {};
  for (const item of matches) {
    errors[item.index] = item.message;
  }
  return errors;
}

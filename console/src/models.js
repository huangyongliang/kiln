/**
 * Save rules for the model catalog page.
 * Display names are checked here before PUT. A 422 is mapped back to the
 * row when the message and the current rows identify it.
 */

export const ROLE_ORDER = ["reader", "builder", "publisher", "admin"];
export const DISPLAY_NAME_MAX = 128;
export const CATALOG_MAX = 32;

export const NAME_TOO_LONG = "显示名不能超过 128 个字符。";
export const NAME_ILLEGAL = "显示名含有非法字符。";
export const ID_TOO_LONG = "模型 id 不能超过 128 个字符，不会截断。";
export const ID_ILLEGAL = "模型 id 含有非法字符。";
export const ID_DUPLICATE = "模型 id 重复。";
export const TOO_MANY = "模型目录不能超过 32 项，不会只保留前 32 项。";
export const SEED_OVER_LIMIT = "环境变量中的模型超过 32 个，保存前请删减";
export const SAVED_TEXT = "已保存";
export const SAVE_FAILED_TAG = "保存失败";
export const UNSAVED_TAG = "尚未保存";

const ILLEGAL_CATALOG_CHAR = /[\p{Cc}\p{Cf}\p{Zl}\p{Zp}\p{Co}\p{Cs}\u3164]/u;

// Trim only Unicode Zs separators, including ASCII space U+0020.
// Do not use String.prototype.trim(): it also strips U+FEFF and does not
// strip U+001C–U+001F, which would disagree with the server.
export function trimCatalogText(value) {
  const chars = [...String(value ?? "")];
  let start = 0;
  let end = chars.length;
  while (start < end && isCatalogSpace(chars[start])) {
    start += 1;
  }
  while (end > start && isCatalogSpace(chars[end - 1])) {
    end -= 1;
  }
  return chars.slice(start, end).join("");
}

function isCatalogSpace(char) {
  return /^\p{Zs}$/u.test(char);
}

export function hasIllegalCatalogText(value) {
  return ILLEGAL_CATALOG_CHAR.test(String(value ?? ""));
}

export function addModelIdError(value) {
  const text = trimCatalogText(value);
  if (hasIllegalCatalogText(text)) {
    return ID_ILLEGAL;
  }
  return "";
}

export function displayNameError(value) {
  const text = trimCatalogText(value);
  if (hasIllegalCatalogText(text)) {
    return NAME_ILLEGAL;
  }
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
      message: noteForRows(nameErrors),
      body: null,
    };
  }
  if (list.length > CATALOG_MAX) {
    return { ok: false, rowErrors: {}, message: TOO_MANY, body: null };
  }
  const idErrors = [];
  for (const [index, row] of list.entries()) {
    const id = trimCatalogText(row?.id);
    if (hasIllegalCatalogText(id)) {
      idErrors.push({ index, id: rowLabel(row, index), message: ID_ILLEGAL });
    }
  }
  if (idErrors.length) {
    return {
      ok: false,
      rowErrors: indexMap(idErrors),
      message: noteForRows(idErrors),
      body: null,
    };
  }
  const seen = new Set();
  for (const row of list) {
    const id = trimCatalogText(row?.id);
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
        id: trimCatalogText(row?.id),
        display_name: trimCatalogText(row?.displayName) || null,
      })),
      default_model: trimCatalogText(globalDefault) || null,
      assignments: ROLE_ORDER.map((roleId) => ({
        role_id: roleId,
        models: [...(assignments?.[roleId] || [])].map((id) => trimCatalogText(id)),
        default_model: trimCatalogText(roleDefaults?.[roleId]) || null,
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
  if (detail === NAME_TOO_LONG || detail === NAME_ILLEGAL) {
    return list.flatMap((row, index) =>
      displayNameError(row?.displayName) === detail
        ? [{ index, id: rowLabel(row, index), message: detail }]
        : [],
    );
  }
  if (detail === ID_ILLEGAL) {
    return list.flatMap((row, index) =>
      hasIllegalCatalogText(trimCatalogText(row?.id))
        ? [{ index, id: rowLabel(row, index), message: detail }]
        : [],
    );
  }
  if (detail === ID_TOO_LONG) {
    return list.flatMap((row, index) =>
      trimCatalogText(row?.id).length > DISPLAY_NAME_MAX
        ? [{ index, id: rowLabel(row, index), message: detail }]
        : [],
    );
  }
  if (detail === ID_DUPLICATE) {
    const counts = new Map();
    for (const row of list) {
      const id = trimCatalogText(row?.id);
      counts.set(id, (counts.get(id) || 0) + 1);
    }
    return list.flatMap((row, index) =>
      counts.get(trimCatalogText(row?.id)) > 1
        ? [{ index, id: rowLabel(row, index), message: detail }]
        : [],
    );
  }
  return [];
}

function rowLabel(row, index) {
  const id = trimCatalogText(row?.id);
  return id || `第 ${index + 1} 项`;
}

function noteForRows(matches) {
  const groups = new Map();
  for (const item of matches) {
    const labels = groups.get(item.message) ?? [];
    if (!labels.includes(item.id)) {
      labels.push(item.id);
    }
    groups.set(item.message, labels);
  }
  return [...groups.entries()]
    .map(([message, labels]) => `${labels.join("、")}：${message}`)
    .join("");
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

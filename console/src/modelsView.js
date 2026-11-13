/**
 * Behavior of the model catalog page that node:test can run without a DOM.
 * ModelsView.vue calls these helpers for add, banners, and unsaved edits.
 */

import {
  CATALOG_MAX,
  DISPLAY_NAME_MAX,
  ID_DUPLICATE,
  ID_TOO_LONG,
  SEED_OVER_LIMIT,
  TOO_MANY,
  addModelIdError,
  codePointLength,
  displayNameError,
  trimCatalogText,
} from "./models.js";

export const ID_EMPTY = "模型 id 不能为空。";
export const LEAVE_MESSAGE = "模型配置有未保存的修改，确定离开？";

export function validateNewModel(rows, draftId, draftName) {
  const list = Array.isArray(rows) ? rows : [];
  const id = trimCatalogText(draftId);
  const displayName = trimCatalogText(draftName);
  if (!id) {
    return { ok: false, field: "id", message: ID_EMPTY };
  }
  if (codePointLength(id) > DISPLAY_NAME_MAX) {
    return { ok: false, field: "id", message: ID_TOO_LONG };
  }
  const illegalId = addModelIdError(id);
  if (illegalId) {
    return { ok: false, field: "id", message: illegalId };
  }
  const nameError = displayNameError(displayName);
  if (nameError) {
    return { ok: false, field: "name", message: nameError };
  }
  if (list.length >= CATALOG_MAX) {
    return { ok: false, field: "", message: TOO_MANY };
  }
  if (list.some((row) => trimCatalogText(row?.id) === id)) {
    return { ok: false, field: "id", message: ID_DUPLICATE };
  }
  return { ok: true, field: "", message: "", id, displayName };
}

export function clearDraftError(error, field) {
  if (!error || error.field !== field) {
    return error || null;
  }
  return null;
}

export function seedBannerFrom(body) {
  if (!body || body.seed_over_limit !== true) {
    return "";
  }
  if (typeof body.seed_notice === "string" && body.seed_notice) {
    return body.seed_notice;
  }
  return SEED_OVER_LIMIT;
}

export function applyEdit(state) {
  return {
    source: state?.source === "db" ? "db" : "env",
    dirty: true,
    saveRejected: false,
    saveNotice: null,
  };
}

export function armBeforeUnload(dirty, event) {
  if (!dirty || !event) {
    return false;
  }
  event.preventDefault();
  event.returnValue = LEAVE_MESSAGE;
  return true;
}

export function confirmRouteLeave(dirty, confirm) {
  if (!dirty) {
    return true;
  }
  return confirm(LEAVE_MESSAGE) !== false;
}

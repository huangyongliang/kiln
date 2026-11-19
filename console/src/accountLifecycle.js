/**
 * Account row actions and the signed-in user's password form.
 * node:test imports this module. The Vue views call the same functions.
 */

import { canDo } from "./permissions.js";

export const DISABLE_CONFIRM_TEXT =
  "停用后，该账号不能再口令登录，已有会话和用户信息同步登录也会被拒绝。确定停用？";

export function statusAction(account) {
  if (account?.status === "disabled") {
    return { id: "status", label: "启用", needsConfirm: false, body: { status: "active" } };
  }
  return { id: "status", label: "停用", needsConfirm: true, body: { status: "disabled" } };
}

export function rowActionLabels(account, perms) {
  if (!canDo("account.disable", perms)) {
    return [];
  }
  return [
    statusAction(account),
    { id: "roles", label: "修改角色", needsConfirm: false },
    { id: "resetPassword", label: "重置口令", needsConfirm: false },
  ];
}

export function conflictText(error) {
  if (!error || error.status !== 409) {
    return "";
  }
  if (typeof error.detail === "string") {
    return error.detail;
  }
  if (typeof error.message === "string") {
    return error.message;
  }
  return "";
}

export function showOwnPassword(account) {
  return Boolean(account && account.id);
}

export function passwordChangeErrors(current, nextPassword) {
  const errors = {};
  if (!String(current ?? "")) {
    errors.current_password = "请填写当前口令";
  }
  const length = [...String(nextPassword ?? "")].length;
  if (length < 8) {
    errors.new_password = "口令至少 8 个字符";
  } else if (length > 128) {
    errors.new_password = "口令过长";
  }
  return errors;
}

export function resetPasswordError(password) {
  const length = [...String(password ?? "")].length;
  if (length < 8) {
    return "口令至少 8 个字符";
  }
  if (length > 128) {
    return "口令过长";
  }
  return "";
}

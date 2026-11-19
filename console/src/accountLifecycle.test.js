import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DISABLE_CONFIRM_TEXT,
  conflictText,
  passwordChangeBody,
  passwordChangeErrors,
  resetPasswordError,
  rowActionLabels,
  showOwnPassword,
  statusAction,
} from "./accountLifecycle.js";

test("account row actions appear only with account.manage", () => {
  const admin = new Set(["account.manage"]);
  const reader = new Set(["account.read"]);
  const labels = rowActionLabels({ status: "active" }, admin).map((item) => item.label);
  assert.deepEqual(labels, ["停用", "修改角色", "重置口令"]);
  assert.deepEqual(rowActionLabels({ status: "active" }, reader), []);
  assert.deepEqual(rowActionLabels({ status: "active" }, new Set(["skill.publish"])), []);
  assert.equal(rowActionLabels({ status: "disabled" }, admin)[0].label, "启用");
});

test("disabling an account asks for confirmation and enabling does not", () => {
  const disable = statusAction({ status: "active" });
  assert.equal(disable.needsConfirm, true);
  assert.deepEqual(disable.body, { status: "disabled" });
  assert.ok(DISABLE_CONFIRM_TEXT.includes("确定停用"));
  const enable = statusAction({ status: "disabled" });
  assert.equal(enable.needsConfirm, false);
  assert.deepEqual(enable.body, { status: "active" });
});

test("a 409 detail is shown unchanged", () => {
  const detail = "不能停用或降级最后一个管理员";
  assert.equal(conflictText({ status: 409, detail }), detail);
  assert.equal(conflictText({ status: 409, message: detail }), detail);
  assert.equal(conflictText({ status: 400, detail: "当前口令不正确" }), "");
  assert.equal(conflictText({ status: 403, detail: "没有权限" }), "");
});

test("changing a password checks the new password length", () => {
  assert.deepEqual(passwordChangeErrors("", "short", "short"), {
    current_password: "请填写当前口令",
    new_password: "口令至少 8 个字符",
  });
  assert.deepEqual(passwordChangeErrors("current-password", "short", "short"), {
    new_password: "口令至少 8 个字符",
  });
  assert.deepEqual(passwordChangeErrors("current-password", "a".repeat(129), "a".repeat(129)), {
    new_password: "口令过长",
  });
  assert.deepEqual(passwordChangeErrors("current-password", "long-enough", "long-enough"), {});
  assert.equal(resetPasswordError("short"), "口令至少 8 个字符");
  assert.equal(resetPasswordError("a".repeat(129)), "口令过长");
  assert.equal(resetPasswordError("long-enough"), "");
});

test("confirming a new password stays in the form and out of the request", () => {
  assert.deepEqual(passwordChangeErrors("current-password", "long-enough", ""), {
    confirm_password: "请再次输入新口令",
  });
  assert.deepEqual(passwordChangeErrors("current-password", "long-enough"), {
    confirm_password: "请再次输入新口令",
  });
  assert.deepEqual(passwordChangeErrors("current-password", "long-enough", "other-value"), {
    confirm_password: "两次输入的新口令不一致",
  });
  assert.deepEqual(passwordChangeErrors("current-password", "long-enough", "long-enough "), {
    confirm_password: "两次输入的新口令不一致",
  });
  assert.deepEqual(passwordChangeErrors("current-password", "short", ""), {
    new_password: "口令至少 8 个字符",
    confirm_password: "请再次输入新口令",
  });
  assert.deepEqual(passwordChangeErrors("current-password", "long-enough", "long-enough"), {});
  const body = passwordChangeBody("current-password", "long-enough");
  assert.deepEqual(Object.keys(body), ["current_password", "new_password"]);
  assert.deepEqual(body, {
    current_password: "current-password",
    new_password: "long-enough",
  });
});

test("the signed-in user can open change-password", () => {
  assert.equal(showOwnPassword({ id: "account-1" }), true);
  assert.equal(showOwnPassword(null), false);
  assert.equal(showOwnPassword({}), false);
});

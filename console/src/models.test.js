import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ID_DUPLICATE,
  ID_TOO_LONG,
  NAME_TOO_LONG,
  SAVE_FAILED_TAG,
  SAVED_TEXT,
  UNSAVED_TAG,
  catalogStatusTag,
  displayNameError,
  mapSaveFailure,
  prepareCatalogSave,
} from "./models.js";

const assignments = { reader: [], builder: ["review-a"], publisher: [], admin: [] };
const roleDefaults = { reader: "", builder: "review-a", publisher: "", admin: "" };

test("a display name of 128 characters is saved and a longer one blocks the row", () => {
  assert.equal(displayNameError("名".repeat(128)), "");
  assert.equal(displayNameError(`  ${"名".repeat(128)}  `), "");
  assert.equal(displayNameError("名".repeat(129)), NAME_TOO_LONG);
  const ok = prepareCatalogSave({
    rows: [{ id: "review-a", displayName: "名".repeat(128) }],
    globalDefault: "review-a",
    assignments,
    roleDefaults,
  });
  assert.equal(ok.ok, true);
  assert.equal(ok.body.models[0].display_name.length, 128);
  const blocked = prepareCatalogSave({
    rows: [
      { id: "review-a", displayName: "评审 A" },
      { id: "review-c", displayName: "名".repeat(129) },
    ],
    globalDefault: "review-a",
    assignments,
    roleDefaults,
  });
  assert.equal(blocked.ok, false);
  assert.equal(blocked.body, null);
  assert.equal(blocked.rowErrors[1], NAME_TOO_LONG);
  assert.equal(blocked.message, `review-c：${NAME_TOO_LONG}`);
});

test("a blank display name is stored as null", () => {
  const prepared = prepareCatalogSave({
    rows: [{ id: "review-a", displayName: "   " }],
    globalDefault: "",
    assignments,
    roleDefaults,
  });
  assert.equal(prepared.ok, true);
  assert.equal(prepared.body.models[0].display_name, null);
  assert.equal(prepared.body.default_model, null);
  assert.equal(prepared.body.assignments[1].default_model, "review-a");
});

test("a server 422 names the row when the current list identifies it", () => {
  const rows = [
    { id: "review-a", displayName: "评审 A" },
    { id: "review-c", displayName: "名".repeat(129) },
    { id: "review-d", displayName: "名".repeat(130) },
  ];
  const mapped = mapSaveFailure(NAME_TOO_LONG, rows);
  assert.equal(mapped.rejected, true);
  assert.equal(mapped.message, `review-c、review-d：${NAME_TOO_LONG}`);
  assert.equal(mapped.rowErrors[1], NAME_TOO_LONG);
  assert.equal(mapped.rowErrors[2], NAME_TOO_LONG);
  assert.equal(mapped.rowErrors[0], undefined);
  const unknown = mapSaveFailure("角色默认必须在该角色的可用模型中。", rows);
  assert.equal(unknown.message, "角色默认必须在该角色的可用模型中。");
  assert.deepEqual(unknown.rowErrors, {});
  const duplicate = mapSaveFailure(ID_DUPLICATE, [
    { id: "review-a", displayName: "" },
    { id: " review-a ", displayName: "" },
  ]);
  assert.equal(duplicate.message, `review-a：${ID_DUPLICATE}`);
  const longId = mapSaveFailure(ID_TOO_LONG, [{ id: "y".repeat(129), displayName: "" }]);
  assert.equal(longId.message.startsWith(`${"y".repeat(129)}：`), true);
});

test("a rejected save does not keep the saved status tag", () => {
  assert.equal(catalogStatusTag("db", false), SAVED_TEXT);
  assert.equal(catalogStatusTag("env", false), UNSAVED_TAG);
  assert.equal(catalogStatusTag("db", true), SAVE_FAILED_TAG);
  assert.equal(catalogStatusTag("env", true), SAVE_FAILED_TAG);
  assert.equal(SAVE_FAILED_TAG.includes(SAVED_TEXT), false);
});

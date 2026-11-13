import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  ID_ILLEGAL,
  ID_TOO_LONG,
  NAME_ILLEGAL,
  NAME_TOO_LONG,
  SAVE_FAILED_TAG,
  SAVED_TEXT,
  SEED_OVER_LIMIT,
  UNSAVED_TAG,
  catalogStatusTag,
  clearRowError,
  codePointLength,
  escapeCatalogId,
  prepareCatalogSave,
  rowErrorField,
} from "./models.js";
import {
  ID_EMPTY,
  LEAVE_MESSAGE,
  applyEdit,
  armBeforeUnload,
  clearDraftError,
  confirmRouteLeave,
  seedBannerFrom,
  validateNewModel,
} from "./modelsView.js";

const root = dirname(fileURLToPath(import.meta.url));
const viewSource = readFileSync(join(root, "views/ModelsView.vue"), "utf8");
const css = readFileSync(join(root, "styles.css"), "utf8");

const assignments = { reader: [], builder: [], publisher: [], admin: [] };
const roleDefaults = { reader: "", builder: "", publisher: "", admin: "" };

test("B1 length checks count code points when adding and saving", () => {
  const emoji = "👍".repeat(128);
  assert.equal(emoji.length, 256);
  assert.equal(codePointLength(emoji), 128);
  const added = validateNewModel([], emoji, emoji);
  assert.equal(added.ok, true);
  assert.equal(added.id, emoji);
  assert.equal(added.displayName, emoji);
  const utf16Trap = "👍".repeat(65);
  assert.equal(utf16Trap.length > 128, true);
  assert.equal(codePointLength(utf16Trap), 65);
  assert.equal(validateNewModel([], utf16Trap, "名").ok, true);
  assert.equal(validateNewModel([], "👍".repeat(129), "名").message, ID_TOO_LONG);
  const saved = prepareCatalogSave({
    rows: [{ id: emoji, displayName: `${"名".repeat(127)}👍` }],
    globalDefault: emoji,
    assignments,
    roleDefaults,
  });
  assert.equal(saved.ok, true);
  assert.equal(codePointLength(saved.body.models[0].display_name), 128);
});

test("B2 addModel validates the display name for illegal characters and 128 code points", () => {
  const illegal = validateNewModel([], "model-a", "通义\u200b千问");
  assert.equal(illegal.ok, false);
  assert.equal(illegal.field, "name");
  assert.equal(illegal.message, NAME_ILLEGAL);
  const tooLong = validateNewModel([], "model-a", "名".repeat(129));
  assert.equal(tooLong.field, "name");
  assert.equal(tooLong.message, NAME_TOO_LONG);
  const emojiTooLong = validateNewModel([], "model-a", "👍".repeat(129));
  assert.equal(emojiTooLong.message, NAME_TOO_LONG);
  const kept = validateNewModel([], "model-a", "👍".repeat(128));
  assert.equal(kept.ok, true);
  assert.equal(kept.displayName, "👍".repeat(128));
  assert.match(viewSource, /validateNewModel\(rows\.value, draftId\.value, draftName\.value\)/);
});

test("B3 an illegal id marks the id input and typing the display name leaves it", () => {
  const added = validateNewModel([], "hidden\u200bmodel", "乙");
  assert.equal(added.field, "id");
  assert.equal(added.message, ID_ILLEGAL);
  const idError = { field: "id", message: ID_ILLEGAL };
  assert.equal(clearDraftError(idError, "name"), idError);
  assert.equal(clearDraftError(idError, "id"), null);
  const empty = validateNewModel([], "   ", "乙");
  assert.equal(empty.field, "id");
  assert.equal(empty.message, ID_EMPTY);
  const nameError = { field: "name", message: NAME_ILLEGAL };
  assert.equal(clearDraftError(nameError, "id"), nameError);
  assert.equal(clearDraftError(nameError, "name"), null);

  const rows = [
    { id: "plain-model", displayName: "甲" },
    { id: "hidden\u200bmodel", displayName: "乙" },
  ];
  const blocked = prepareCatalogSave({
    rows,
    globalDefault: "plain-model",
    assignments: { ...assignments, builder: ["plain-model"] },
    roleDefaults: { ...roleDefaults, builder: "plain-model" },
  });
  assert.equal(rowErrorField(blocked.rowErrors[1]), "id");
  assert.equal(rowErrorField(blocked.rowErrors[0]), "");
  const still = clearRowError(blocked.rowErrors, 1, "name");
  assert.equal(still[1], ID_ILLEGAL);
  const cleared = clearRowError(blocked.rowErrors, 1, "id");
  assert.equal(cleared[1], undefined);
  assert.match(viewSource, /draftError\?\.field === 'id'/);
  assert.match(viewSource, /draftError\?\.field === 'name'/);
  assert.match(viewSource, /rowField\(index\) === 'id'/);
  assert.match(viewSource, /rowField\(index\) === 'name'/);
  assert.match(viewSource, /onDisplayNameInput\(index\)/);
  assert.equal(viewSource.includes("clearRowError(index)"), false);
});

test("B4 a multi-row save note separates rows", () => {
  const mixed = prepareCatalogSave({
    rows: [
      { id: "env-00", displayName: "名\u0007称" },
      { id: "env-01", displayName: "名".repeat(129) },
    ],
    globalDefault: "env-00",
    assignments,
    roleDefaults,
  });
  assert.equal(mixed.message, `env-00：${NAME_ILLEGAL}\nenv-01：${NAME_TOO_LONG}`);
  const same = prepareCatalogSave({
    rows: [
      { id: "a\u200bb", displayName: "甲" },
      { id: "c\u200bd", displayName: "乙" },
    ],
    globalDefault: "",
    assignments,
    roleDefaults,
  });
  assert.equal(
    same.message,
    `${escapeCatalogId("a\u200bb")}、${escapeCatalogId("c\u200bd")}：${ID_ILLEGAL}`,
  );
  assert.match(css, /\.save-note \{[^}]*white-space:\s*pre-line/);
});

test("B5 saved and failed notes clear on the next edit", () => {
  const saved = {
    source: "db",
    dirty: false,
    saveRejected: false,
    saveNotice: { kind: "ok", text: SAVED_TEXT },
  };
  assert.equal(catalogStatusTag(saved.source, saved.saveRejected, saved.dirty), SAVED_TEXT);
  const edited = applyEdit(saved);
  assert.equal(edited.saveNotice, null);
  assert.equal(edited.saveRejected, false);
  assert.equal(edited.dirty, true);
  assert.equal(catalogStatusTag(edited.source, edited.saveRejected, edited.dirty), UNSAVED_TAG);
  const failed = {
    source: "db",
    dirty: true,
    saveRejected: true,
    saveNotice: { kind: "error", text: "保存失败" },
  };
  assert.equal(catalogStatusTag(failed.source, failed.saveRejected, failed.dirty), SAVE_FAILED_TAG);
  const after = applyEdit(failed);
  assert.equal(after.saveNotice, null);
  assert.equal(after.saveRejected, false);
  assert.equal(catalogStatusTag(after.source, after.saveRejected, after.dirty), UNSAVED_TAG);
  const note = css.slice(css.indexOf(".save-note {"), css.indexOf(".save-note.error"));
  const margin = /scroll-margin-top:\s*(\d+)px/.exec(note);
  assert.ok(margin, note);
  assert.ok(Number(margin[1]) >= 52);
  assert.match(viewSource, /applyEdit\(/);
});

test("B6 the over-32 banner uses seed_notice and the warn style", () => {
  assert.equal(seedBannerFrom({ seed_over_limit: true, seed_notice: "请先删到 32 个" }), "请先删到 32 个");
  assert.equal(seedBannerFrom({ seed_over_limit: true, seed_notice: null }), SEED_OVER_LIMIT);
  assert.equal(seedBannerFrom({ seed_over_limit: true }), SEED_OVER_LIMIT);
  assert.equal(seedBannerFrom({ seed_over_limit: false, seed_notice: "请先删到 32 个" }), "");
  assert.equal(seedBannerFrom(null), "");
  assert.match(viewSource, /class="banner warn"/);
  assert.match(viewSource, /seedBannerFrom\(body\)/);
  assert.equal(viewSource.includes("SEED_OVER_LIMIT"), false);
  const warn = css.match(/\.banner\.warn \{[^}]+\}/);
  assert.ok(warn);
  assert.match(warn[0], /var\(--gold\)/);
  assert.equal(warn[0].includes("var(--clay)"), false);
});

test("B7 unsaved edits confirm on beforeunload and when leaving the route", () => {
  assert.equal(
    confirmRouteLeave(false, () => {
      throw new Error("clean page should not ask");
    }),
    true,
  );
  let asked = "";
  assert.equal(
    confirmRouteLeave(true, (message) => {
      asked = message;
      return false;
    }),
    false,
  );
  assert.equal(asked, LEAVE_MESSAGE);
  assert.equal(confirmRouteLeave(true, () => true), true);
  const idle = { prevented: false, returnValue: "" };
  assert.equal(
    armBeforeUnload(false, {
      preventDefault() {
        idle.prevented = true;
      },
      returnValue: "",
    }),
    false,
  );
  assert.equal(idle.prevented, false);
  const event = {
    prevented: false,
    returnValue: "",
    preventDefault() {
      this.prevented = true;
    },
  };
  assert.equal(armBeforeUnload(true, event), true);
  assert.equal(event.prevented, true);
  assert.equal(event.returnValue, LEAVE_MESSAGE);
  assert.match(viewSource, /addEventListener\("beforeunload", onBeforeUnload\)/);
  assert.match(viewSource, /onBeforeRouteLeave\(/);
  assert.match(viewSource, /armBeforeUnload\(dirty\.value, event\)/);
  assert.match(viewSource, /confirmRouteLeave\(dirty\.value/);
});

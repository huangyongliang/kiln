import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  ID_DUPLICATE,
  ID_ILLEGAL,
  ID_TOO_LONG,
  NAME_ILLEGAL,
  NAME_TOO_LONG,
  SAVE_FAILED_TAG,
  SAVED_TEXT,
  SEED_OVER_LIMIT,
  UNSAVED_TAG,
  addModelIdError,
  catalogStatusTag,
  displayNameError,
  mapSaveFailure,
  prepareCatalogSave,
} from "./models.js";

const LISTED_ILLEGAL = [
  "\u007f",
  "\u0085",
  "\u00ad",
  "\u061c",
  "\u200c",
  "\u200d",
  "\u200e",
  "\u200f",
  "\u2028",
  "\u2029",
  "\u3164",
  "\u{E0001}",
];

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

test("control, invisible, and bidi characters are rejected and CJK names pass", () => {
  for (const sample of ["\u0000", "\u0007", "\u200b", "\u202e"]) {
    assert.equal(displayNameError(`通义${sample}千问`), NAME_ILLEGAL);
    const badId = prepareCatalogSave({
      rows: [{ id: `review-${sample}a`, displayName: "通义千问" }],
      globalDefault: "",
      assignments,
      roleDefaults,
    });
    assert.equal(badId.ok, false);
    assert.equal(badId.body, null);
    assert.equal(badId.rowErrors[0], ID_ILLEGAL);
    assert.equal(badId.message, `review-${sample}a：${ID_ILLEGAL}`);
  }
  assert.equal(displayNameError("通义千问"), "");
  const saved = prepareCatalogSave({
    rows: [{ id: "review-a", displayName: "通义千问" }],
    globalDefault: "review-a",
    assignments,
    roleDefaults,
  });
  assert.equal(saved.ok, true);
  assert.equal(saved.body.models[0].display_name, "通义千问");
  const mapped = mapSaveFailure(NAME_ILLEGAL, [{ id: "review-a", displayName: "通\u200b义" }]);
  assert.equal(mapped.message, `review-a：${NAME_ILLEGAL}`);
  assert.equal(SEED_OVER_LIMIT, "环境变量中的模型超过 32 个，保存前请删减");
});

test("listed format characters are rejected and CJK names still pass", () => {
  for (const sample of LISTED_ILLEGAL) {
    assert.equal(displayNameError(`通义${sample}千问`), NAME_ILLEGAL);
    assert.equal(addModelIdError(`model-${sample}a`), ID_ILLEGAL);
    const prepared = prepareCatalogSave({
      rows: [{ id: `model-${sample}a`, displayName: "通义千问" }],
      globalDefault: "",
      assignments,
      roleDefaults,
    });
    assert.equal(prepared.ok, false);
    assert.equal(prepared.rowErrors[0], ID_ILLEGAL);
    assert.equal(prepared.message, `model-${sample}a：${ID_ILLEGAL}`);
  }
  assert.equal(displayNameError("通义千问"), "");
  assert.equal(addModelIdError("model-tongyi"), "");
  const saved = prepareCatalogSave({
    rows: [{ id: "model-tongyi", displayName: "通义千问" }],
    globalDefault: "model-tongyi",
    assignments,
    roleDefaults,
  });
  assert.equal(saved.ok, true);
  assert.equal(saved.body.models[0].id, "model-tongyi");
  assert.equal(saved.body.models[0].display_name, "通义千问");
});

test("the save note uses the row's own error text", () => {
  const illegal = prepareCatalogSave({
    rows: [{ id: "env-00", displayName: "名\u0007称" }],
    globalDefault: "env-00",
    assignments,
    roleDefaults,
  });
  assert.equal(illegal.ok, false);
  assert.equal(illegal.rowErrors[0], NAME_ILLEGAL);
  assert.equal(illegal.message, `env-00：${NAME_ILLEGAL}`);
  const mixed = prepareCatalogSave({
    rows: [
      { id: "env-00", displayName: "名\u0007称" },
      { id: "env-01", displayName: "名".repeat(129) },
    ],
    globalDefault: "env-00",
    assignments,
    roleDefaults,
  });
  assert.equal(mixed.rowErrors[0], NAME_ILLEGAL);
  assert.equal(mixed.rowErrors[1], NAME_TOO_LONG);
  assert.equal(mixed.message.includes(`env-00：${NAME_ILLEGAL}`), true);
  assert.equal(mixed.message.includes(`env-01：${NAME_TOO_LONG}`), true);
  assert.equal(mixed.message.includes(NAME_TOO_LONG) && mixed.message.startsWith(`env-00：${NAME_ILLEGAL}`), true);
});

test("an illegal model id is rejected at add time and names the row on save", () => {
  assert.equal(addModelIdError("hidden\u200bmodel"), ID_ILLEGAL);
  assert.equal(addModelIdError("  hidden\u200bmodel  "), ID_ILLEGAL);
  assert.equal(addModelIdError("plain-model"), "");
  const blocked = prepareCatalogSave({
    rows: [
      { id: "plain-model", displayName: "甲" },
      { id: "hidden\u200bmodel", displayName: "乙" },
    ],
    globalDefault: "plain-model",
    assignments,
    roleDefaults,
  });
  assert.equal(blocked.ok, false);
  assert.equal(blocked.body, null);
  assert.equal(blocked.rowErrors[0], undefined);
  assert.equal(blocked.rowErrors[1], ID_ILLEGAL);
  assert.equal(blocked.message, `hidden\u200bmodel：${ID_ILLEGAL}`);
  const mapped = mapSaveFailure(ID_ILLEGAL, [
    { id: "plain-model", displayName: "" },
    { id: "hidden\u200bmodel", displayName: "" },
  ]);
  assert.equal(mapped.rejected, true);
  assert.equal(mapped.rowErrors[1], ID_ILLEGAL);
  assert.equal(mapped.rowErrors[0], undefined);
  assert.equal(mapped.message, `hidden\u200bmodel：${ID_ILLEGAL}`);
});

test("gpt-4o lookalikes are illegal and a visible emoji name is kept", () => {
  assert.equal(addModelIdError("gpt-4o"), "");
  assert.equal(displayNameError("眼袋 🫩"), "");
  assert.equal(displayNameError("☺️"), "");
  const saved = prepareCatalogSave({
    rows: [{ id: "gpt-4o", displayName: "眼袋 🫩" }],
    globalDefault: "gpt-4o",
    assignments: { reader: [], builder: ["gpt-4o"], publisher: [], admin: [] },
    roleDefaults: { reader: "", builder: "gpt-4o", publisher: "", admin: "" },
  });
  assert.equal(saved.ok, true);
  assert.equal(saved.body.models[0].id, "gpt-4o");
  assert.equal(saved.body.models[0].display_name, "眼袋 🫩");
  for (const codePoint of [0xe0080, 0xe0000, 0xe01f0, 0x2065, 0xfff0]) {
    const id = `gpt-4o${String.fromCodePoint(codePoint)}`;
    assert.equal(addModelIdError(id), ID_ILLEGAL, codePoint.toString(16));
  }
});

test("catalog trim matches the shared parity fixture", () => {
  const fixture = JSON.parse(
    readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "../../backend/tests/fixtures/catalog_trim_parity.json"),
      "utf8",
    ),
  );
  const idBase = fixture.idBase;
  const nameBase = fixture.nameBase;
  for (const item of fixture.cases) {
    const sample = String.fromCodePoint(Number.parseInt(item.cp, 16));
    for (const where of ["start", "interior", "end"]) {
      const verdict = item[where];
      const idText = placeCatalogText(idBase, sample, where);
      const nameText = placeCatalogText(nameBase, sample, where);
      if (verdict === "reject") {
        assert.equal(addModelIdError(idText), ID_ILLEGAL, `${item.cp} ${where} id`);
        assert.equal(displayNameError(nameText), NAME_ILLEGAL, `${item.cp} ${where} name`);
        const blockedId = prepareCatalogSave({
          rows: [{ id: idText, displayName: "通义千问" }],
          globalDefault: idText,
          assignments: parityAssignments(idText),
          roleDefaults: parityRoleDefaults(idText),
        });
        assert.equal(blockedId.ok, false);
        assert.equal(blockedId.rowErrors[0], ID_ILLEGAL);
        const blockedName = prepareCatalogSave({
          rows: [{ id: idBase, displayName: nameText }],
          globalDefault: idBase,
          assignments: parityAssignments(idBase),
          roleDefaults: parityRoleDefaults(idBase),
        });
        assert.equal(blockedName.ok, false);
        assert.equal(blockedName.rowErrors[0], NAME_ILLEGAL);
        continue;
      }
      const expectedId = verdict === "trim" ? idBase : idText;
      const expectedName = verdict === "trim" ? nameBase : nameText;
      assert.equal(addModelIdError(idText), "", `${item.cp} ${where} id`);
      assert.equal(displayNameError(nameText), "", `${item.cp} ${where} name`);
      const savedId = prepareCatalogSave({
        rows: [{ id: idText, displayName: "通义千问" }],
        globalDefault: idText,
        assignments: parityAssignments(idText),
        roleDefaults: parityRoleDefaults(idText),
      });
      assert.equal(savedId.ok, true, `${item.cp} ${where} id save`);
      assert.equal(savedId.body.models[0].id, expectedId);
      assert.equal(savedId.body.default_model, expectedId);
      assert.deepEqual(savedId.body.assignments[1].models, [expectedId]);
      assert.equal(savedId.body.assignments[1].default_model, expectedId);
      const savedName = prepareCatalogSave({
        rows: [{ id: idBase, displayName: nameText }],
        globalDefault: idBase,
        assignments: parityAssignments(idBase),
        roleDefaults: parityRoleDefaults(idBase),
      });
      assert.equal(savedName.ok, true, `${item.cp} ${where} name save`);
      assert.equal(savedName.body.models[0].display_name, expectedName);
    }
  }
});

function placeCatalogText(base, sample, where) {
  if (where === "start") {
    return sample + base;
  }
  if (where === "end") {
    return base + sample;
  }
  const chars = [...base];
  const mid = Math.floor(chars.length / 2);
  return chars.slice(0, mid).join("") + sample + chars.slice(mid).join("");
}

function parityAssignments(id) {
  return { reader: [], builder: [id], publisher: [], admin: [] };
}

function parityRoleDefaults(id) {
  return { reader: "", builder: id, publisher: "", admin: "" };
}

test("a rejected save does not keep the saved status tag", () => {
  assert.equal(catalogStatusTag("db", false), SAVED_TEXT);
  assert.equal(catalogStatusTag("env", false), UNSAVED_TAG);
  assert.equal(catalogStatusTag("db", true), SAVE_FAILED_TAG);
  assert.equal(catalogStatusTag("env", true), SAVE_FAILED_TAG);
  assert.equal(SAVE_FAILED_TAG.includes(SAVED_TEXT), false);
});

import assert from "node:assert/strict";
import { test } from "node:test";

import { canBuildWithSkill, canReviseSkill, skillListPath } from "./skillLists.js";

const MINE = "11111111-1111-4111-8111-111111111111";
const OTHER = "22222222-2222-4222-8222-222222222222";

test("sidebar requests include scope", () => {
  const mine = skillListPath("mine");
  const usable = skillListPath("usable");
  assert.match(mine, /[?&]scope=mine(?:&|$)/);
  assert.match(usable, /[?&]scope=usable(?:&|$)/);
  assert.equal(mine.includes("scope=usable"), false);
  assert.equal(usable.includes("scope=mine"), false);
  assert.throws(() => skillListPath("all"), /未知的 Skill 范围/);
});

test("a draft is editable by its author or by someone who can publish", () => {
  const draft = { status: "draft", created_by: MINE };
  const returned = { status: "draft", created_by: MINE, review_note: "请改" };
  const foreign = { status: "draft", created_by: OTHER };
  const historical = { status: "draft", created_by: null };
  const published = { status: "published", created_by: MINE };
  const builder = new Set(["skill.use", "skill.submit"]);
  const publisher = new Set(["skill.use", "skill.publish"]);
  const reader = new Set(["skill.read"]);

  assert.equal(canReviseSkill(draft, builder, MINE), true);
  assert.equal(canReviseSkill(returned, builder, MINE), true);
  assert.equal(canReviseSkill(foreign, builder, MINE), false);
  assert.equal(canReviseSkill(historical, builder, MINE), false);
  assert.equal(canReviseSkill(published, builder, MINE), false);
  assert.equal(canReviseSkill(historical, publisher, OTHER), true);
  assert.equal(canReviseSkill(foreign, publisher, OTHER), true);
  assert.equal(canReviseSkill(published, publisher, OTHER), false);
  assert.equal(canReviseSkill(draft, reader, MINE), false);
  assert.equal(canReviseSkill(draft, builder, ""), false);
});

test("a published skill can open a build when the user may run one", () => {
  const published = { status: "published" };
  const draft = { status: "draft" };
  const builder = new Set(["skill.use", "executor.run"]);
  const reader = new Set(["skill.read"]);
  assert.equal(canBuildWithSkill(published, builder), true);
  assert.equal(canBuildWithSkill(draft, builder), false);
  assert.equal(canBuildWithSkill(published, reader), false);
});

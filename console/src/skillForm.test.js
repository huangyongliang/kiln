import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));

test("skill form textareas are tall enough to edit", () => {
  const vue = readFileSync(join(root, "components/SkillDraftForm.vue"), "utf8");
  const css = readFileSync(join(root, "styles.css"), "utf8");
  assert.match(vue, /\["description", "说明", "skill-description",\s+([6-9]|[1-9]\d)\]/);
  assert.match(vue, /\["output_spec", "输出说明", "skill-output",\s+([6-9]|[1-9]\d)\]/);
  assert.match(vue, /\["allowed_dependencies", "允许依赖", "skill-deps",\s+([6-9]|[1-9]\d),/);
  for (const match of vue.matchAll(/\brows="(\d+)"/g)) {
    assert.ok(Number(match[1]) >= 6, match[0]);
  }
  const rule = css.match(/\.skill-form textarea \{[^}]+\}/);
  assert.ok(rule);
  const height = /min-height:\s*(\d+(?:\.\d+)?)rem/.exec(rule[0]);
  assert.ok(height, rule[0]);
  assert.ok(Number(height[1]) >= 8);
  assert.match(rule[0], /resize:\s*vertical/);
});

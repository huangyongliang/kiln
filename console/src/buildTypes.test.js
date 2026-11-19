import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  catalogCodeError,
  catalogFieldErrors,
  catalogLabelError,
  catalogPayload,
  jenkinsLabelError,
} from "./buildTypes.js";

const root = dirname(fileURLToPath(import.meta.url));
const view = readFileSync(join(root, "views/BuildTypesView.vue"), "utf8");
const builds = readFileSync(join(root, "views/BuildsView.vue"), "utf8");
const artifacts = readFileSync(join(root, "views/ArtifactsView.vue"), "utf8");
const skillForm = readFileSync(join(root, "components/SkillDraftForm.vue"), "utf8");

test("catalog form rejects a bad code, a long label, and label expressions", () => {
  assert.equal(catalogCodeError("node"), "");
  assert.equal(catalogCodeError("Node") !== "", true);
  assert.equal(catalogCodeError("node", { editing: true }), "");
  assert.equal(catalogLabelError("  Node  "), "");
  assert.equal(catalogLabelError(""), "标签不能为空");
  assert.equal(catalogLabelError("名".repeat(41)), "标签过长");
  assert.equal(jenkinsLabelError(""), "");
  assert.equal(jenkinsLabelError("linux"), "");
  assert.equal(jenkinsLabelError("Built-In Node"), "");
  assert.equal(jenkinsLabelError("a && b"), "Jenkins 标签不能使用 && 或 ||");
  assert.equal(jenkinsLabelError("a||b"), "Jenkins 标签不能使用 && 或 ||");
  const errors = catalogFieldErrors({
    code: "node",
    label: "Node",
    description: "",
    jenkinsLabel: "linux",
    conditions: [
      { tool: "node", version: "20" },
      { tool: "node", version: "18" },
    ],
    editing: false,
  });
  assert.equal(errors["condition-1"], "每种工具只能有一条版本条件");
  const body = catalogPayload({
    code: " node ",
    label: " Node ",
    description: " ",
    enabled: true,
    jenkinsLabel: "  ",
    conditions: [{ tool: "node", version: "20" }],
  });
  assert.equal(body.code, "node");
  assert.equal(body.label, "Node");
  assert.equal(body.description, "");
  assert.equal(body.jenkins_label, null);
  assert.deepEqual(body.runtime_conditions, [{ tool: "node", version: "20" }]);
});

test("admin and start-build surfaces read the catalog instead of a fixed type list", () => {
  assert.match(view, /include_disabled=true/);
  assert.match(view, /\/api\/v1\/build-package-types/);
  assert.match(view, /catalogFieldErrors/);
  assert.doesNotMatch(view, /前端构建包/);
  assert.match(builds, /readonly/);
  assert.match(builds, /buildSubmitErrorTarget/);
  assert.match(builds, /build_package_type_label/);
  assert.doesNotMatch(builds, /PACKAGE_OPTIONS/);
  assert.match(builds, /buildRepositoryChoices/);
  assert.match(artifacts, /\/api\/v1\/build-package-types/);
  assert.doesNotMatch(artifacts, /PACKAGE_OPTIONS/);
  assert.match(skillForm, /build_package_type/);
  assert.match(skillForm, /这里不能新增/);
  assert.match(skillForm, /jenkins_label/);
  assert.doesNotMatch(skillForm, /前端构建包/);
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import {
  BUILD_OWNED_REASON,
  FOREIGN_BYTES_TEXT,
  FOREIGN_VERSION_TEXT,
  MACHINE_OR_HISTORY,
  actorLabel,
  contentConflictText,
  reservationBody,
  uploadAllowed,
} from "./actors.js";

const root = dirname(fileURLToPath(import.meta.url));
const artifactView = readFileSync(join(root, "views/ArtifactDetailView.vue"), "utf8");
const artifactsView = readFileSync(join(root, "views/ArtifactsView.vue"), "utf8");
const buildView = readFileSync(join(root, "views/BuildDetailView.vue"), "utf8");

const NEXUS_COPY =
  "平台自管目录、不可变版本和制品字节。字节保存在平台本地存储，不发布、不同步到 Nexus。版本入库后可以取回这些字节。";

test("actor labels use the display name, the id, or the null copy", () => {
  assert.equal(actorLabel(null, []), MACHINE_OR_HISTORY);
  assert.equal(actorLabel(undefined, [{ id: "acc-1", display_name: "构建者甲" }]), MACHINE_OR_HISTORY);
  assert.equal(actorLabel("", []), MACHINE_OR_HISTORY);
  assert.equal(MACHINE_OR_HISTORY, "机器令牌或历史记录");
  assert.equal(actorLabel("acc-1", [{ id: "acc-1", display_name: "构建者甲" }]), "构建者甲");
  assert.equal(actorLabel("acc-1", []), "acc-1");
  assert.equal(actorLabel("acc-1", null), "acc-1");
  assert.equal(actorLabel("missing", [{ id: "acc-1", display_name: "构建者甲" }]), "missing");
  assert.equal(actorLabel("acc-1", [{ id: "acc-1", display_name: "  " }]), "acc-1");
});

test("build detail and artifact detail render the actor through actorLabel", () => {
  assert.match(buildView, /actorLabel\(build\.requested_by, accounts\)/);
  assert.match(buildView, /contentConflictText\(build\)/);
  assert.match(artifactView, /actorLabel\(item\.created_by, accounts\)/);
  assert.match(artifactView, /actorLabel\(box\.reservation\.account_id, accounts\)/);
  assert.equal(contentConflictText({ content_conflict: "foreign_bytes" }), FOREIGN_BYTES_TEXT);
  assert.equal(contentConflictText({ content_conflict: "foreign_version" }), FOREIGN_VERSION_TEXT);
  assert.notEqual(FOREIGN_BYTES_TEXT, FOREIGN_VERSION_TEXT);
  assert.equal(contentConflictText({ content_conflict: null }), "");
  assert.equal(contentConflictText(null), "");
  assert.match(buildView, /v-if="contentConflictText\(build\)" class="banner warn"/);
});

test("a build-owned artifact does not offer upload", () => {
  assert.equal(uploadAllowed({ build_owned: true }, true), false);
  assert.equal(uploadAllowed({ build_owned: false }, true), true);
  assert.equal(uploadAllowed({ build_owned: false }, false), false);
  assert.equal(BUILD_OWNED_REASON, "该制品由产品构建产出，不接受手工登记或上传");
  assert.match(artifactView, /uploadAllowed\(artifact\.value, canUpload\.value\)/);
  assert.match(artifactView, /v-if="artifact && uploadOffered"/);
  assert.match(artifactView, /!item\.content_available && uploadOffered/);
  assert.match(artifactView, /BUILD_OWNED_REASON/);
});

test("a logged-in reservation body does not carry a different requester", () => {
  const loggedIn = reservationBody({ username: "ada_builder" }, "someone_else", 900);
  assert.equal(Object.hasOwn(loggedIn, "requester"), false);
  assert.equal(loggedIn.ttl_seconds, 900);
  const sameName = reservationBody({ username: "ada_builder" }, "ada_builder", 900);
  assert.equal(Object.hasOwn(sameName, "requester"), false);
  const machine = reservationBody(null, "ops_user", 3600);
  assert.equal(machine.requester, "ops_user");
  assert.equal(machine.ttl_seconds, 3600);
  assert.match(artifactView, /reservationBody\(account\.value, box\.requester, box\.ttl\)/);
});

test("upload conflict reloads the artifact and retrieve shows the source", () => {
  assert.match(artifactView, /caught\?\.status === 409/);
  assert.match(artifactView, /await load\(\)/);
  const conflict = artifactView.slice(artifactView.indexOf("catch (caught)"));
  assert.match(conflict, /status === 409[\s\S]{0,120}await load\(\)/);
  assert.match(artifactView, /<dt>来源<\/dt>\s*<dd>\{\{ sourceLabel\(box\.target\?\.source\) \}\}<\/dd>/);
});

test("the artifact list describes platform-owned bytes and does not promise Nexus", () => {
  assert.match(artifactsView, new RegExp(NEXUS_COPY.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.equal(artifactsView.includes("Nexus 集成还没有接上"), false);
  assert.equal(artifactsView.includes("尚未接入"), false);
});

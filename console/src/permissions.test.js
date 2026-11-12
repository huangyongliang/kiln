import assert from "node:assert/strict";
import { test } from "node:test";

import { canDo, canSee, permsFrom, requiredCode } from "./permissions.js";

const ROUTES = [
  "overview",
  "builds",
  "build",
  "builds-detail",
  "artifacts",
  "artifact",
  "artifact-detail",
  "skills",
  "skill",
  "skill-detail",
  "access",
  "chat",
];

const ACTIONS = [
  "executor.read",
  "executor.run",
  "job.create",
  "git.read",
  "agent.qa",
  "chat.send",
  "agent.draft_skill",
  "agent.build_artifact",
  "skill.submit",
  "skill.publish",
  "artifact.read",
  "skill.read",
  "account.read",
  "overview.builds",
  "overview.skills",
  "overview.artifacts",
];

test("unknown permissions show every route and action", () => {
  for (const perms of [null, undefined]) {
    for (const route of ROUTES) {
      assert.equal(canSee(route, perms), true, route);
    }
    for (const action of ACTIONS) {
      assert.equal(canDo(action, perms), true, action);
    }
  }
});

test("permsFrom reads /auth/me permissions", () => {
  const perms = permsFrom({
    permissions: ["executor.read", "skill.submit"],
    account: { username: "ada", role_ids: ["reader"] },
  });
  assert.equal(perms.has("executor.read"), true);
  assert.equal(perms.has("skill.submit"), true);
  assert.equal(perms.has("skill.publish"), false);
  assert.equal(perms.has("account.read"), false);
});

test("permsFrom unions permissions of the account roles only", () => {
  const perms = permsFrom({
    account: { username: "ada", role_ids: ["reader", "builder"] },
    roles: [
      { id: "reader", permissions: ["account.read", "skill.read", "artifact.read"] },
      {
        id: "builder",
        permissions: ["executor.read", "executor.run", "git.read", "agent.qa", "agent.build_artifact"],
      },
      { id: "admin", permissions: ["account.manage", "skill.publish"] },
    ],
  });
  assert.equal(perms.has("account.read"), true);
  assert.equal(perms.has("executor.run"), true);
  assert.equal(perms.has("git.read"), true);
  assert.equal(perms.has("agent.build_artifact"), true);
  assert.equal(perms.has("account.manage"), false);
  assert.equal(perms.has("skill.publish"), false);
});

test("permsFrom returns null when the source is incomplete", () => {
  assert.equal(permsFrom(null), null);
  assert.equal(permsFrom(undefined), null);
  assert.equal(permsFrom("reader"), null);
  assert.equal(permsFrom({}), null);
  assert.equal(permsFrom({ account: { role_ids: ["reader"] } }), null);
  assert.equal(permsFrom({ roles: [] }), null);
});

test("an empty permission list is known and hides gated controls", () => {
  const perms = permsFrom({ permissions: [] });
  assert.ok(perms);
  assert.equal(perms.size, 0);
  assert.equal(canSee("builds", perms), false);
  assert.equal(canSee("chat", perms), false);
  assert.equal(canDo("skill.submit", perms), false);
  assert.equal(canSee("overview", perms), true);
});

test("build routes and the overview builds block need executor.read", () => {
  const reader = new Set(["account.read", "skill.read", "artifact.read"]);
  assert.equal(canSee("builds", reader), false);
  assert.equal(canSee("build", reader), false);
  assert.equal(canSee("builds-detail", reader), false);
  assert.equal(canDo("overview.builds", reader), false);
  assert.equal(requiredCode("builds"), "executor.read");
  assert.equal(requiredCode("build"), "executor.read");

  const executor = new Set(["executor.read"]);
  assert.equal(canSee("builds", executor), true);
  assert.equal(canSee("build", executor), true);
  assert.equal(canDo("overview.builds", executor), true);
  assert.equal(canDo("job.create", executor), false);
  assert.equal(canDo("executor.run", executor), false);
});

test("creating a compat job needs executor.run", () => {
  assert.equal(canDo("job.create", new Set(["executor.run"])), true);
  assert.equal(canDo("job.create", new Set(["executor.read"])), false);
});

test("code source and repository list need git.read", () => {
  assert.equal(canDo("git.read", new Set(["git.read"])), true);
  assert.equal(canDo("git.read", new Set(["executor.read", "executor.run"])), false);
});

test("chat nav, route, and send need agent.qa", () => {
  const reader = new Set(["account.read"]);
  assert.equal(canSee("chat", reader), false);
  assert.equal(canDo("chat.send", reader), false);
  assert.equal(requiredCode("chat"), "agent.qa");

  const qa = new Set(["agent.qa"]);
  assert.equal(canSee("chat", qa), true);
  assert.equal(canDo("chat.send", qa), true);
  assert.equal(canDo("agent.qa", qa), true);
});

test("suggestion chips use their own agent permissions", () => {
  const qa = new Set(["agent.qa"]);
  assert.equal(canDo("agent.draft_skill", qa), false);
  assert.equal(canDo("agent.build_artifact", qa), false);
  assert.equal(canDo("agent.draft_skill", new Set(["agent.draft_skill"])), true);
  assert.equal(canDo("agent.build_artifact", new Set(["agent.build_artifact"])), true);
  assert.equal(canDo("agent.draft_skill", new Set(["agent.build_artifact"])), false);
});

test("submitting a skill needs skill.submit or skill.publish", () => {
  assert.equal(canDo("skill.submit", new Set(["skill.submit"])), true);
  assert.equal(canDo("skill.publish", new Set(["skill.submit"])), false);
  assert.equal(canDo("skill.submit", new Set(["skill.publish"])), true);
  assert.equal(canDo("skill.publish", new Set(["skill.publish"])), true);
  assert.equal(canDo("skill.submit", new Set(["skill.use", "skill.read"])), false);
  assert.equal(canDo("skill.publish", new Set(["skill.use"])), false);
});

test("artifact, skill, and account pages use their read codes", () => {
  const none = new Set();
  assert.equal(canSee("artifacts", none), false);
  assert.equal(canSee("artifact", none), false);
  assert.equal(canSee("artifact-detail", none), false);
  assert.equal(canSee("skills", none), false);
  assert.equal(canSee("skill", none), false);
  assert.equal(canSee("skill-detail", none), false);
  assert.equal(canSee("access", none), false);
  assert.equal(canDo("overview.artifacts", none), false);
  assert.equal(canDo("overview.skills", none), false);

  assert.equal(canSee("artifacts", new Set(["artifact.read"])), true);
  assert.equal(canSee("artifact", new Set(["artifact.read"])), true);
  assert.equal(canSee("skills", new Set(["skill.read"])), true);
  assert.equal(canSee("skill", new Set(["skill.read"])), true);
  assert.equal(canSee("access", new Set(["account.read"])), true);
  assert.equal(requiredCode("artifacts"), "artifact.read");
  assert.equal(requiredCode("skills"), "skill.read");
  assert.equal(requiredCode("access"), "account.read");
  assert.equal(requiredCode("overview"), "");
});

test("unknown actions stay hidden once permissions are known", () => {
  assert.equal(canDo("account.manage", new Set(["account.manage"])), false);
  assert.equal(canDo("not-a-gate", null), true);
});

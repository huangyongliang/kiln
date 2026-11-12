/**
 * Pure permission checks for the console.
 * `null` permissions means the session is unknown (OIDC or machine token
 * before /auth/me) and every gate stays visible.
 */

const ROUTE_GATES = {
  builds: "executor.read",
  build: "executor.read",
  "builds-detail": "executor.read",
  artifacts: "artifact.read",
  artifact: "artifact.read",
  "artifact-detail": "artifact.read",
  skills: "skill.read",
  skill: "skill.read",
  "skill-detail": "skill.read",
  access: "account.read",
  chat: "agent.qa",
};

const ACTION_GATES = {
  "executor.read": ["executor.read"],
  "executor.run": ["executor.run"],
  "job.create": ["executor.run"],
  "git.read": ["git.read"],
  "agent.qa": ["agent.qa"],
  "chat.send": ["agent.qa"],
  "agent.draft_skill": ["agent.draft_skill"],
  "agent.build_artifact": ["agent.build_artifact"],
  "skill.submit": ["skill.submit", "skill.publish"],
  "skill.publish": ["skill.publish"],
  "artifact.read": ["artifact.read"],
  "skill.read": ["skill.read"],
  "account.read": ["account.read"],
  "overview.builds": ["executor.read"],
  "overview.skills": ["skill.read"],
  "overview.artifacts": ["artifact.read"],
};

export function permsFrom(input) {
  if (input == null || typeof input !== "object") {
    return null;
  }
  if (Array.isArray(input.permissions)) {
    return new Set(input.permissions.filter((code) => typeof code === "string"));
  }
  const account = input.account;
  const roles = input.roles;
  if (account && Array.isArray(account.role_ids) && Array.isArray(roles)) {
    const wanted = new Set(account.role_ids);
    const codes = new Set();
    for (const role of roles) {
      if (!role || !wanted.has(role.id) || !Array.isArray(role.permissions)) {
        continue;
      }
      for (const code of role.permissions) {
        if (typeof code === "string") {
          codes.add(code);
        }
      }
    }
    return codes;
  }
  return null;
}

export function requiredCode(route) {
  return ROUTE_GATES[route] || "";
}

export function canSee(route, perms) {
  if (perms == null) {
    return true;
  }
  const code = ROUTE_GATES[route];
  if (!code) {
    return true;
  }
  return perms.has(code);
}

export function canDo(action, perms) {
  if (perms == null) {
    return true;
  }
  const codes = ACTION_GATES[action];
  if (!codes) {
    return false;
  }
  return codes.some((code) => perms.has(code));
}

/**
 * Pure permission checks for the console.
 * `null` permissions means the session is unknown (OIDC or machine token
 * before /auth/me) and every gate stays visible.
 */

export const ROUTE_GATES = {
  builds: "executor.read",
  build: "executor.read",
  "builds-detail": "executor.read",
  artifacts: "artifact.read",
  artifact: "artifact.read",
  "artifact-detail": "artifact.read",
  skills: "skill.read",
  skill: "skill.read",
  "skill-detail": "skill.read",
  "skill-new": "skill.use",
  access: "account.read",
  models: "llm.manage",
  chat: "agent.qa",
};

export const ACTION_GATES = {
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
  "skill.create": ["skill.use"],
  "skill.reject": ["skill.publish"],
  "artifact.read": ["artifact.read"],
  "skill.read": ["skill.read"],
  "account.read": ["account.read"],
  "account.create": ["account.manage"],
  "overview.builds": ["executor.read"],
  "overview.skills": ["skill.read"],
  "overview.artifacts": ["artifact.read"],
  "build.create": ["executor.run"],
  "artifact.create": ["artifact.upload"],
  "artifact.upload": ["artifact.upload"],
  "artifact.reserve": ["artifact.download"],
  "retrieve.download": ["artifact.download"],
  "llm.catalog.update": ["llm.manage"],
};

export const GATE_DOCS = [
  { action: "account.create", label: "新建账号", codes: ["account.manage"] },
  { action: "skill.create", label: "起草 Skill", codes: ["skill.use"] },
  { action: "skill.submit", label: "提交审核", codes: ["skill.submit", "skill.publish"] },
  { action: "skill.publish", label: "发布", codes: ["skill.publish"] },
  { action: "skill.reject", label: "退回草稿", codes: ["skill.publish"] },
  { action: "executor.run", label: "发起任务或产品构建", codes: ["executor.run"] },
  { action: "job.create", label: "发起任务", codes: ["executor.run"] },
  { action: "build.create", label: "发起产品构建", codes: ["executor.run"] },
  { action: "artifact.create", label: "登记制品", codes: ["artifact.upload"] },
  { action: "artifact.upload", label: "上传字节", codes: ["artifact.upload"] },
  { action: "artifact.reserve", label: "预约下载", codes: ["artifact.download"] },
  { action: "retrieve.download", label: "取回字节", codes: ["artifact.download"] },
  { action: "chat.send", label: "对话", codes: ["agent.qa"] },
  { action: "llm.catalog.update", label: "保存模型配置", codes: ["llm.manage"] },
  { action: "agent.draft_skill", label: "对话起草 Skill", codes: ["agent.draft_skill"] },
  { action: "agent.build_artifact", label: "对话制作制品", codes: ["agent.build_artifact"] },
];

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

/**
 * Chat sidebar queries. The lists come from the skill API, not from the thread.
 */

import { canDo } from "./permissions.js";

export function skillListPath(scope) {
  if (scope !== "mine" && scope !== "usable") {
    throw new Error("未知的 Skill 范围");
  }
  return `/api/v1/skills?scope=${encodeURIComponent(scope)}`;
}

export function canReviseSkill(skill, perms, accountId) {
  if (!skill || skill.status !== "draft") {
    return false;
  }
  if (canDo("skill.publish", perms)) {
    return true;
  }
  if (!canDo("skill.create", perms)) {
    return false;
  }
  return typeof accountId === "string" && accountId.length > 0 && skill.created_by === accountId;
}

export function canBuildWithSkill(skill, perms) {
  return Boolean(
    skill &&
      skill.status === "published" &&
      canDo("executor.run", perms) &&
      canDo("skill.create", perms),
  );
}

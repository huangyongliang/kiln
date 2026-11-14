/**
 * Actor labels and upload/reservation decisions shared by the build and artifact pages.
 * node:test imports this module. The Vue views call the same functions.
 */

export const MACHINE_OR_HISTORY = "机器令牌或历史记录";
export const BUILD_OWNED_REASON = "该制品由产品构建产出，不接受手工登记或上传";
export const FOREIGN_BYTES_TEXT = "该版本已有非构建来源的字节，不能当作构建产物";
export const FOREIGN_VERSION_TEXT = "该版本已登记为非构建来源，还没有字节，不能当作构建产物";

export function actorLabel(accountId, accounts) {
  if (accountId == null || accountId === "") {
    return MACHINE_OR_HISTORY;
  }
  const id = String(accountId);
  if (!Array.isArray(accounts)) {
    return id;
  }
  const match = accounts.find((item) => item && String(item.id) === id);
  const name = typeof match?.display_name === "string" ? match.display_name.trim() : "";
  return name || id;
}

export function uploadAllowed(artifact, canUpload) {
  if (!canUpload || artifact?.build_owned) {
    return false;
  }
  return true;
}

export function reservationBody(account, requester, ttlSeconds) {
  const body = { ttl_seconds: Number(ttlSeconds) };
  if (account && typeof account.username === "string" && account.username) {
    return body;
  }
  const who = String(requester ?? "").trim();
  if (who) {
    body.requester = who;
  }
  return body;
}

export function contentConflictText(build) {
  if (build?.content_conflict === "foreign_bytes") {
    return FOREIGN_BYTES_TEXT;
  }
  if (build?.content_conflict === "foreign_version") {
    return FOREIGN_VERSION_TEXT;
  }
  return "";
}

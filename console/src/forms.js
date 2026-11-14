/**
 * Client-side checks mirrored from the API models.
 * The service remains the authority; these strings are hints only.
 * Lengths use code points, matching Python len().
 */

const USERNAME = /^[a-z][a-z0-9_-]{2,31}$/;
const VERSION = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$/;
const ARTIFACT_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$/;
const ARTIFACT_GROUP = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const INPUT_NAME = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const BUILD_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const REPO_SEGMENT = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const REF = /^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$/;
const PACKAGE_TYPES = new Set(["maven", "python", "generic", "frontend"]);
const STEP_KINDS = new Set(["checkout", "compile", "package", "archive"]);

const TYPE_TEXT = {
  missing: "必填",
  string_pattern_mismatch: "格式不正确",
  string_too_long: "过长",
  string_too_short: "过短",
  extra_forbidden: "包含不允许的字段",
};

const VALUE_PREFIX = "Value error, ";
const LOC_PREFIXES = new Set(["body", "query", "path", "header", "cookie"]);

export const UPLOAD_LIMIT_BYTES = 1048576;

export function codePointLength(value) {
  return [...String(value ?? "")].length;
}

function isCc(char) {
  const code = char.codePointAt(0);
  return code <= 0x1f || code === 0x7f || (code >= 0x80 && code <= 0x9f);
}

export function controlCharError(text, allowNewline = false) {
  const allowed = allowNewline ? new Set(["\n", "\t"]) : new Set();
  for (const char of String(text ?? "")) {
    if (!isCc(char) || allowed.has(char)) {
      continue;
    }
    return "含有非法字符";
  }
  return "";
}

function lowControlError(text) {
  for (const char of String(text ?? "")) {
    if (char.codePointAt(0) < 32) {
      return "含有非法字符";
    }
  }
  return "";
}

export function usernameError(value) {
  if (!USERNAME.test(String(value ?? ""))) {
    return "用户名须以小写字母开头，只能含小写字母、数字、下划线和连字符，长度 3–32";
  }
  return "";
}

export function displayNameError(value) {
  const text = String(value ?? "").trim();
  if (!text) {
    return "显示名不能为空";
  }
  if (codePointLength(text) > 80) {
    return "显示名过长";
  }
  if (lowControlError(text)) {
    return "显示名含有非法字符";
  }
  return "";
}

export function emailError(value) {
  if (value == null || String(value).trim() === "") {
    return "";
  }
  const text = String(value).trim();
  if (!text.includes("@") || text.startsWith("@") || text.endsWith("@") || text.includes(" ")) {
    return "邮箱格式不正确";
  }
  return "";
}

export function passwordError(value) {
  if (value == null || value === "") {
    return "";
  }
  const length = codePointLength(value);
  if (length < 8) {
    return "口令至少 8 个字符";
  }
  if (length > 128) {
    return "口令过长";
  }
  return "";
}

export function skillNameError(value) {
  const text = String(value ?? "").trim();
  const length = codePointLength(text);
  if (length < 2) {
    return "名称至少 2 个字符";
  }
  if (length > 80) {
    return "名称过长";
  }
  if (lowControlError(text)) {
    return "名称含有非法字符";
  }
  return "";
}

export function versionError(value) {
  if (!VERSION.test(String(value ?? ""))) {
    return "版本格式不正确";
  }
  return "";
}

export function artifactNameError(value) {
  if (!ARTIFACT_NAME.test(String(value ?? ""))) {
    return "名称格式不正确";
  }
  return "";
}

export function groupError(value) {
  if (value == null || String(value).trim() === "") {
    return "";
  }
  if (!ARTIFACT_GROUP.test(String(value).trim())) {
    return "组格式不正确";
  }
  return "";
}

export function inputNameError(value) {
  if (!INPUT_NAME.test(String(value ?? ""))) {
    return "输入名格式不正确";
  }
  return "";
}

export function boundError(value, max, message) {
  if (codePointLength(String(value ?? "")) > max) {
    return message;
  }
  return "";
}

export function scopeList(value) {
  return String(value ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

export function scopeListError(value) {
  const lines = scopeList(value);
  if (lines.length > 50) {
    return "范围条目过多";
  }
  if (lines.some((line) => codePointLength(line) > 200)) {
    return "范围条目过长";
  }
  return "";
}

export function repositoryError(value) {
  const text = String(value ?? "").trim();
  const length = codePointLength(text);
  if (length < 3) {
    return "仓库至少 3 个字符";
  }
  if (length > 400) {
    return "仓库过长";
  }
  const parts = text.split("/");
  if (parts.length < 2 || parts.some((part) => !REPO_SEGMENT.test(part))) {
    return "仓库必须是 owner/name";
  }
  return "";
}

export function refError(value) {
  const text = String(value ?? "").trim();
  const length = codePointLength(text);
  if (length < 1) {
    return "引用不能为空";
  }
  if (length > 255) {
    return "引用过长";
  }
  if (
    !REF.test(text) ||
    text.startsWith("/") ||
    text.endsWith("/") ||
    text.includes("..") ||
    text.includes("//")
  ) {
    return "引用无效";
  }
  return "";
}

export function buildNameError(value) {
  const text = String(value ?? "").trim();
  if (!text) {
    return "";
  }
  if (!BUILD_NAME.test(text)) {
    return "任务名格式不正确";
  }
  return "";
}

export function packageTypeError(value) {
  if (!PACKAGE_TYPES.has(value)) {
    return "类型不正确";
  }
  return "";
}

export function stepKindError(value) {
  if (!STEP_KINDS.has(value)) {
    return "步骤类型不正确";
  }
  return "";
}

export function rejectReasonError(value) {
  const text = String(value ?? "").trim();
  if (!text) {
    return "退回原因不能为空";
  }
  if (codePointLength(text) > 500) {
    return "退回原因过长";
  }
  if (controlCharError(text, true)) {
    return "退回原因含有非法字符";
  }
  return "";
}

export function checksumError(value, algorithm = "sha256") {
  const name = algorithm === "sha512" ? "sha512" : "sha256";
  const length = name === "sha512" ? 128 : 64;
  const text = String(value ?? "").trim();
  if (!new RegExp(`^[0-9a-fA-F]{${length}}$`).test(text)) {
    return `${name} 需要 ${length} 位十六进制字符串`;
  }
  return "";
}

export function uploadSizeError(size) {
  if (Number(size) > UPLOAD_LIMIT_BYTES) {
    return "控制台单次上传上限 1 MB，更大的字节由产品构建产出";
  }
  return "";
}

export function ttlError(value) {
  const number = Number(value);
  if (!Number.isInteger(number) || number < 60 || number > 86400) {
    return "有效期不正确";
  }
  return "";
}

export function fieldMessage(item) {
  const raw = item && typeof item.msg === "string" ? item.msg : "";
  if (raw.startsWith(VALUE_PREFIX)) {
    return raw.slice(VALUE_PREFIX.length) || "参数不正确";
  }
  if (item && Object.prototype.hasOwnProperty.call(TYPE_TEXT, item.type)) {
    return TYPE_TEXT[item.type];
  }
  return raw || "参数不正确";
}

export function fieldKey(loc) {
  if (!Array.isArray(loc)) {
    return "";
  }
  for (let index = loc.length - 1; index >= 0; index -= 1) {
    const part = loc[index];
    if (typeof part === "string" && !LOC_PREFIXES.has(part)) {
      return part;
    }
  }
  return "";
}

export function fieldErrors(detail) {
  if (!Array.isArray(detail)) {
    return {};
  }
  const errors = {};
  for (const item of detail) {
    const key = fieldKey(item?.loc);
    if (!key) {
      continue;
    }
    const message = fieldMessage(item);
    errors[key] = errors[key] ? `${errors[key]}；${message}` : message;
  }
  return errors;
}

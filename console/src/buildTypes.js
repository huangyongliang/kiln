/**
 * Checks for the build-type catalog form.
 * The service remains the authority.
 */

const CODE = /^[a-z][a-z0-9_]{0,31}$/;
const VERSION = /^[0-9][0-9A-Za-z._+-]{0,31}$/;
export const RUNTIME_TOOLS = [
  ["jdk", "JDK"],
  ["maven", "Maven"],
  ["node", "Node"],
  ["python", "Python"],
];

function illegal(text) {
  for (const char of String(text ?? "")) {
    const code = char.codePointAt(0);
    if (code < 32 || code === 127 || (code >= 0x80 && code <= 0x9f)) {
      return true;
    }
    if ((code >= 0x202a && code <= 0x202e) || (code >= 0x2066 && code <= 0x2069)) {
      return true;
    }
    if (code === 0x200b || code === 0x2060 || code === 0xfeff) {
      return true;
    }
  }
  return false;
}

export function catalogCodeError(value, { editing = false } = {}) {
  if (editing) {
    return "";
  }
  const text = String(value ?? "").trim();
  if (!CODE.test(text)) {
    return "编码须以小写字母开头，只能含小写字母、数字和下划线";
  }
  return "";
}

export function catalogLabelError(value) {
  const text = String(value ?? "").trim();
  if (!text) {
    return "标签不能为空";
  }
  if ([...text].length > 40) {
    return "标签过长";
  }
  if (illegal(text)) {
    return "标签含有非法字符";
  }
  return "";
}

export function catalogDescriptionError(value) {
  const text = String(value ?? "").trim();
  if ([...text].length > 500) {
    return "说明过长";
  }
  if (illegal(text)) {
    return "说明含有非法字符";
  }
  return "";
}

export function jenkinsLabelError(value) {
  const text = String(value ?? "").trim();
  if (!text) {
    return "";
  }
  if ([...text].length > 128) {
    return "Jenkins 标签过长";
  }
  if (illegal(text)) {
    return "Jenkins 标签含有非法字符";
  }
  if (text.includes("&&") || text.includes("||")) {
    return "Jenkins 标签不能使用 && 或 ||";
  }
  return "";
}

export function conditionVersionError(value) {
  const text = String(value ?? "").trim();
  if (!VERSION.test(text) || illegal(text)) {
    return "版本格式不正确";
  }
  return "";
}

export function catalogFieldErrors({ code, label, description, jenkinsLabel, conditions, editing }) {
  const errors = {};
  const codeMessage = catalogCodeError(code, { editing });
  if (codeMessage) errors.code = codeMessage;
  const labelMessage = catalogLabelError(label);
  if (labelMessage) errors.label = labelMessage;
  const descriptionMessage = catalogDescriptionError(description);
  if (descriptionMessage) errors.description = descriptionMessage;
  const jenkinsMessage = jenkinsLabelError(jenkinsLabel);
  if (jenkinsMessage) errors.jenkins_label = jenkinsMessage;
  const seen = new Set();
  (conditions || []).forEach((row, index) => {
    if (!RUNTIME_TOOLS.some((item) => item[0] === row.tool)) {
      errors[`condition-${index}`] = "工具不正确";
      return;
    }
    if (seen.has(row.tool)) {
      errors[`condition-${index}`] = "每种工具只能有一条版本条件";
      return;
    }
    seen.add(row.tool);
    const versionMessage = conditionVersionError(row.version);
    if (versionMessage) errors[`condition-${index}`] = versionMessage;
  });
  if ((conditions || []).length > 4) {
    errors.runtime_conditions = "运行时条件过多";
  }
  return errors;
}

export function catalogPayload({ code, label, description, enabled, jenkinsLabel, conditions }) {
  return {
    code: String(code ?? "").trim(),
    label: String(label ?? "").trim(),
    description: String(description ?? "").trim(),
    enabled: Boolean(enabled),
    jenkins_label: String(jenkinsLabel ?? "").trim() || null,
    runtime_conditions: (conditions || []).map((row) => ({
      tool: row.tool,
      version: String(row.version ?? "").trim(),
    })),
  };
}

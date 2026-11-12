const PACKAGE_LABELS = {
  maven: "Maven",
  python: "Python",
  generic: "通用包",
  frontend: "前端构建包",
};

const SOURCE_LABELS = {
  build: "构建",
  upload: "上传",
};

const STEP_LABELS = {
  checkout: "检出",
  compile: "编译",
  package: "打包",
  archive: "归档",
};

export function formatTime(value) {
  if (!value) {
    return "—";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "—";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(date);
}

export function shortSha(value) {
  if (!value) {
    return "—";
  }
  return String(value).slice(0, 7);
}

export function shortDigest(value) {
  const text = String(value || "");
  if (text.length < 12) {
    return text || "—";
  }
  return `${text.slice(0, 6)}…${text.slice(-4)}`;
}

export function formatBytes(value) {
  if (value == null || Number.isNaN(Number(value))) {
    return "—";
  }
  const size = Number(value);
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(size < 10 * 1024 ? 1 : 0)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function packageLabel(value) {
  return PACKAGE_LABELS[value] || value || "—";
}

export function sourceLabel(value) {
  return SOURCE_LABELS[value] || value || "—";
}

export function stepLabel(value) {
  return STEP_LABELS[value] || value || "步骤";
}

export function latestVersion(artifact) {
  const versions = artifact?.versions || [];
  if (!versions.length) {
    return null;
  }
  return [...versions].sort((left, right) => {
    return new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
  })[0];
}

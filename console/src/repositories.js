/**
 * Skill-draft repository field.
 * The catalog is the same git source the build form already reads.
 * An empty selection means the build is not limited to a repository.
 */

import { repositoryError, scopeList, scopeListError } from "./forms.js";

export const REPO_NOTICES = {
  forbidden: "没有代码源读取权限。可以手写 owner/name，或留空。",
  unconfigured: "代码源未配置。可以手写 owner/name，或留空。",
  source: "无法读取代码源。可以手写 owner/name，或留空。",
  list: "仓库列表读取失败。可以手写 owner/name，或留空。",
};

export const REPO_UNRESTRICTED = "不勾选表示构建时不限制仓库。";
export const REPO_MANUAL_UNRESTRICTED = "留空表示构建时不限制仓库。";
export const DEPENDENCY_NOTE = "依赖仍按一行一条填写，这里不做目录勾选。";

export async function fetchGitCatalog(getJson) {
  let source;
  try {
    source = await getJson("/api/v1/git/source");
  } catch (caught) {
    return {
      source: null,
      items: [],
      problem: caught?.status === 403 ? "forbidden" : "source",
      caught,
    };
  }
  if (!source?.configured) {
    return { source: source ?? null, items: [], problem: "unconfigured", caught: null };
  }
  try {
    const body = await getJson("/api/v1/git/repositories");
    return {
      source,
      items: Array.isArray(body?.items) ? body.items : [],
      problem: null,
      caught: null,
    };
  } catch (caught) {
    if (caught?.status === 503) {
      return { source, items: [], problem: "unconfigured", caught };
    }
    return { source, items: [], problem: "list", caught };
  }
}

export function repositoryFullNames(items) {
  const names = [];
  const seen = new Set();
  for (const item of items || []) {
    const name = typeof item?.full_name === "string" ? item.full_name.trim() : "";
    if (!name || seen.has(name)) {
      continue;
    }
    seen.add(name);
    names.push(name);
  }
  return names;
}

/**
 * Catalog names first, then draft values that are not in this response.
 * Opening the form does not drop those draft values.
 */
export function repositoryChecklist(catalogNames, draftNames) {
  const rows = [];
  const seen = new Set();
  for (const name of catalogNames || []) {
    const text = String(name ?? "").trim();
    if (!text || seen.has(text)) {
      continue;
    }
    seen.add(text);
    rows.push({ name: text, extra: false });
  }
  for (const name of draftNames || []) {
    const text = String(name ?? "").trim();
    if (!text || seen.has(text)) {
      continue;
    }
    seen.add(text);
    rows.push({ name: text, extra: true });
  }
  return rows;
}

/**
 * Checked names, in checklist order, plus one optional manual owner/name.
 * An empty selection and a blank manual line yield [].
 */
export function allowedRepositoriesFromChecklist(selected, manual, choices) {
  const picked = new Set(
    (Array.isArray(selected) ? selected : [])
      .map((name) => String(name ?? "").trim())
      .filter(Boolean),
  );
  const values = [];
  const seen = new Set();
  const add = (name) => {
    const text = String(name ?? "").trim();
    if (!text || seen.has(text) || !picked.has(text)) {
      return;
    }
    seen.add(text);
    values.push(text);
  };
  const rows = Array.isArray(choices) ? choices : [];
  if (rows.length) {
    for (const row of rows) {
      add(typeof row === "string" ? row : row?.name);
    }
  }
  for (const name of picked) {
    add(name);
  }
  const extra = String(manual ?? "").trim();
  if (extra && !seen.has(extra)) {
    seen.add(extra);
    values.push(extra);
  }
  return values;
}

export function checklistRepositoriesError(selected, manual, choices) {
  const extra = String(manual ?? "").trim();
  if (extra) {
    const format = repositoryError(extra);
    if (format) {
      return format;
    }
  }
  return scopeListError(allowedRepositoriesFromChecklist(selected, extra, choices).join("\n"));
}

export function repositoryLinesError(value) {
  for (const line of scopeList(value)) {
    const format = repositoryError(line);
    if (format) {
      return format;
    }
  }
  return scopeListError(value);
}

export function repositoryFieldState(result, draftValues) {
  const draft = Array.isArray(draftValues)
    ? draftValues.map((item) => String(item ?? "").trim()).filter(Boolean)
    : scopeList(draftValues);
  if (!result || result.problem) {
    const problem = result?.problem || "source";
    return {
      mode: "manual",
      notice: REPO_NOTICES[problem] || REPO_NOTICES.source,
      choices: [],
      selected: draft,
    };
  }
  const choices = repositoryChecklist(repositoryFullNames(result.items), draft);
  return {
    mode: "checklist",
    notice: "",
    choices,
    selected: draft.filter((name) => choices.some((row) => row.name === name)),
  };
}

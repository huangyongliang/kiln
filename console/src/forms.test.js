import assert from "node:assert/strict";
import { test } from "node:test";

import {
  allowedRepositoriesFromChecklist,
  checklistRepositoriesError,
  fetchGitCatalog,
  repositoryFieldState,
  repositoryLinesError,
} from "./repositories.js";
import {
  artifactNameError,
  boundError,
  buildNameError,
  checksumError,
  codePointLength,
  controlCharError,
  displayNameError,
  emailError,
  fieldErrors,
  fieldMessage,
  groupError,
  inputNameError,
  packageTypeError,
  passwordError,
  refError,
  rejectReasonError,
  repositoryError,
  scopeList,
  scopeListError,
  skillNameError,
  stepKindError,
  uploadSizeError,
  UPLOAD_LIMIT_BYTES,
  usernameError,
  versionError,
} from "./forms.js";

test("username matches the account pattern", () => {
  assert.equal(usernameError("ada"), "");
  assert.equal(usernameError("a1_b-c"), "");
  assert.equal(usernameError("ab"), "用户名须以小写字母开头，只能含小写字母、数字、下划线和连字符，长度 3–32");
  assert.equal(usernameError("Ada"), usernameError("ab"));
  assert.equal(usernameError(`a${"b".repeat(31)}`), "");
  assert.equal(usernameError(`a${"b".repeat(32)}`) !== "", true);
  assert.equal(usernameError("a/b"), usernameError("ab"));
});

test("display name trims, counts code points, and rejects controls below space", () => {
  assert.equal(displayNameError("  甲  "), "");
  assert.equal(displayNameError("   "), "显示名不能为空");
  assert.equal(displayNameError("字".repeat(80)), "");
  assert.equal(displayNameError("字".repeat(81)), "显示名过长");
  assert.equal(displayNameError("甲\n乙"), "显示名含有非法字符");
  assert.equal(displayNameError("甲\u007f乙"), "");
  assert.equal(codePointLength("👍"), 1);
  assert.equal(displayNameError(`👍${"字".repeat(79)}`), "");
});

test("email is optional and rejects a missing local or domain part", () => {
  assert.equal(emailError(""), "");
  assert.equal(emailError("  "), "");
  assert.equal(emailError(null), "");
  assert.equal(emailError(" ada@example.com "), "");
  assert.equal(emailError("ada"), "邮箱格式不正确");
  assert.equal(emailError("@example.com"), "邮箱格式不正确");
  assert.equal(emailError("ada@"), "邮箱格式不正确");
  assert.equal(emailError("ada @example.com"), "邮箱格式不正确");
});

test("password is optional and uses an 8 to 128 code-point span", () => {
  assert.equal(passwordError(""), "");
  assert.equal(passwordError(null), "");
  assert.equal(passwordError("1234567"), "口令至少 8 个字符");
  assert.equal(passwordError("12345678"), "");
  assert.equal(passwordError("👍".repeat(8)), "");
  assert.equal(passwordError("a".repeat(128)), "");
  assert.equal(passwordError("a".repeat(129)), "口令过长");
});

test("skill name matches the cleaned length and control rules", () => {
  assert.equal(skillNameError(" 构建 "), "");
  assert.equal(skillNameError(" a "), "名称至少 2 个字符");
  assert.equal(skillNameError("👍甲"), "");
  assert.equal(skillNameError("👍"), "名称至少 2 个字符");
  assert.equal(skillNameError("字".repeat(80)), "");
  assert.equal(skillNameError("字".repeat(81)), "名称过长");
  assert.equal(skillNameError("名\u0000称"), "名称含有非法字符");
});

test("version, artifact name, and group use the catalog patterns", () => {
  assert.equal(versionError("0.1.0"), "");
  assert.equal(versionError("v1.2.3-rc+1"), "");
  assert.equal(versionError(".1"), "版本格式不正确");
  assert.equal(versionError(`a${"b".repeat(63)}`), "");
  assert.equal(versionError(`a${"b".repeat(64)}`), "版本格式不正确");
  assert.equal(artifactNameError("lib"), "");
  assert.equal(artifactNameError("a"), "名称格式不正确");
  assert.equal(artifactNameError("a/b"), "名称格式不正确");
  assert.equal(groupError(""), "");
  assert.equal(groupError("com.kiln"), "");
  assert.equal(groupError("com/kiln"), "组格式不正确");
});

test("skill input names and bounded text", () => {
  assert.equal(inputNameError("branch"), "");
  assert.equal(inputNameError("A_1"), "");
  assert.equal(inputNameError("1branch"), "输入名格式不正确");
  assert.equal(inputNameError("a-b"), "输入名格式不正确");
  assert.equal(boundError("字".repeat(300), 300, "说明过长"), "");
  assert.equal(boundError("字".repeat(301), 300, "说明过长"), "说明过长");
});

test("scope lists drop blank lines and cap count and length", () => {
  assert.deepEqual(scopeList(" group/demo \n\n maven \n"), ["group/demo", "maven"]);
  assert.equal(scopeListError("group/demo"), "");
  assert.equal(scopeListError(` ${"a".repeat(201)} `), "范围条目过长");
  assert.equal(scopeListError(`${"a\n".repeat(50)}b`), "范围条目过多");
});

test("repository and ref follow the build parsers", () => {
  assert.equal(repositoryError("group/demo"), "");
  assert.equal(repositoryError("ab"), "仓库至少 3 个字符");
  assert.equal(repositoryError("group"), "仓库必须是 owner/name");
  assert.equal(repositoryError("group/demo/app"), "");
  assert.equal(repositoryError(`aa/${"b".repeat(401)}`), "仓库过长");
  assert.equal(refError("main"), "");
  assert.equal(refError("feature/demo"), "");
  assert.equal(refError(""), "引用不能为空");
  assert.equal(refError("/main"), "引用无效");
  assert.equal(refError("a..b"), "引用无效");
  assert.equal(refError("a".repeat(256)), "引用过长");
});

test("optional build name, package type, and step kind", () => {
  assert.equal(buildNameError(""), "");
  assert.equal(buildNameError("nightly"), "");
  assert.equal(buildNameError("有空格"), "任务名格式不正确");
  assert.equal(packageTypeError("maven"), "");
  assert.equal(packageTypeError("deb"), "类型不正确");
  assert.equal(stepKindError("checkout"), "");
  assert.equal(stepKindError("shell"), "步骤类型不正确");
});

test("reject reason trims, counts code points, and allows newline and tab only", () => {
  assert.equal(rejectReasonError("  原因  "), "");
  assert.equal(rejectReasonError("   "), "退回原因不能为空");
  assert.equal(rejectReasonError("字".repeat(500)), "");
  assert.equal(rejectReasonError(`  ${"字".repeat(500)}  `), "");
  assert.equal(rejectReasonError("字".repeat(501)), "退回原因过长");
  assert.equal(rejectReasonError("第一行\n第二行\t续"), "");
  assert.equal(rejectReasonError("有\u0000空"), "退回原因含有非法字符");
  assert.equal(rejectReasonError("有\r回车"), "退回原因含有非法字符");
  assert.equal(rejectReasonError("有\u0085控制"), "退回原因含有非法字符");
  assert.equal(controlCharError("a\nb", false), "含有非法字符");
  assert.equal(controlCharError("a\nb", true), "");
});

test("checksum and upload size", () => {
  assert.equal(checksumError("ab".repeat(32), "sha256"), "");
  assert.equal(checksumError("AB".repeat(32), "sha256"), "");
  assert.equal(checksumError("abcd", "sha256"), "sha256 需要 64 位十六进制字符串");
  assert.equal(checksumError("ab".repeat(64), "sha512"), "");
  assert.equal(checksumError("ab".repeat(32), "sha512"), "sha512 需要 128 位十六进制字符串");
  assert.equal(uploadSizeError(UPLOAD_LIMIT_BYTES), "");
  assert.equal(
    uploadSizeError(UPLOAD_LIMIT_BYTES + 1),
    "控制台上传上限 1 MB（nginx 限制），更大的字节由产品构建产出",
  );
});

test("fieldErrors strips the value-error prefix and maps loc to a field", () => {
  const detail = [
    { type: "value_error", loc: ["body", "reason"], msg: "Value error, 退回原因过长" },
    {
      type: "string_pattern_mismatch",
      loc: ["body", "username"],
      msg: "String should match pattern '^[a-z][a-z0-9_-]{2,31}$'",
    },
    { type: "missing", loc: ["body", "display_name"], msg: "Field required" },
    {
      type: "string_too_long",
      loc: ["body", "inputs", 0, "description"],
      msg: "String should have at most 300 characters",
    },
    {
      type: "string_too_short",
      loc: ["body", "password"],
      msg: "String should have at least 8 characters",
    },
    { type: "extra_forbidden", loc: ["body", "extra"], msg: "Extra inputs are not permitted" },
    { type: "value_error", loc: ["body", "name"], msg: "Value error, 名称至少 2 个字符" },
  ];
  assert.equal(fieldMessage(detail[0]), "退回原因过长");
  const errors = fieldErrors(detail);
  assert.equal(errors.reason, "退回原因过长");
  assert.equal(errors.username, "格式不正确");
  assert.equal(errors.display_name, "必填");
  assert.equal(errors.description, "过长");
  assert.equal(errors.password, "过短");
  assert.equal(errors.extra, "包含不允许的字段");
  assert.equal(errors.name, "名称至少 2 个字符");
  assert.deepEqual(fieldErrors("不是数组"), {});
  assert.equal(JSON.stringify(errors).includes("Value error"), false);
});

test("repository checkboxes map full_name and keep values outside the list", () => {
  const result = {
    problem: null,
    items: [
      { full_name: "ada/demo" },
      { full_name: "ada/other" },
      { full_name: "ada/demo" },
      { full_name: "  " },
    ],
  };
  const state = repositoryFieldState(result, ["ada/demo", "kept/legacy"]);
  assert.equal(state.mode, "checklist");
  assert.equal(state.notice, "");
  assert.deepEqual(state.choices, [
    { name: "ada/demo", extra: false },
    { name: "ada/other", extra: false },
    { name: "kept/legacy", extra: true },
  ]);
  assert.deepEqual(state.selected, ["ada/demo", "kept/legacy"]);
  assert.deepEqual(
    allowedRepositoriesFromChecklist(state.selected, "", state.choices),
    ["ada/demo", "kept/legacy"],
  );
  assert.deepEqual(allowedRepositoriesFromChecklist([], "", state.choices), []);
  assert.deepEqual(
    allowedRepositoriesFromChecklist(["ada/other"], " ada/manual ", state.choices),
    ["ada/other", "ada/manual"],
  );
  assert.equal(checklistRepositoriesError(["ada/demo"], "", state.choices), "");
  assert.equal(checklistRepositoriesError([], "  ", state.choices), "");
  assert.equal(
    checklistRepositoriesError(["ada/demo"], "nope", state.choices),
    "仓库必须是 owner/name",
  );
  const uncheckedExtra = allowedRepositoriesFromChecklist(["ada/demo"], "", state.choices);
  assert.equal(uncheckedExtra.includes("kept/legacy"), false);
  assert.equal(state.choices.some((row) => row.name === "kept/legacy"), true);
});

test("without a repository list the field stays manual and can be empty", async () => {
  for (const problem of ["forbidden", "unconfigured", "source", "list"]) {
    const state = repositoryFieldState({ items: [], problem }, ["ada/demo"]);
    assert.equal(state.mode, "manual");
    assert.equal(state.notice.length > 0, true);
    assert.deepEqual(state.choices, []);
    assert.deepEqual(state.selected, ["ada/demo"]);
    assert.deepEqual(scopeList(state.selected.join("\n")), ["ada/demo"]);
  }
  const empty = repositoryFieldState({ items: [], problem: "unconfigured" }, " \n ");
  assert.deepEqual(empty.selected, []);
  assert.equal(repositoryLinesError(""), "");
  assert.equal(repositoryLinesError("ada/demo\n\n"), "");
  assert.deepEqual(scopeList(""), []);
  assert.deepEqual(scopeList("ada/demo\n\n"), ["ada/demo"]);
  assert.equal(repositoryLinesError("not-a-repo"), "仓库必须是 owner/name");

  const sourceCalls = [];
  const unconfigured = await fetchGitCatalog(async (path) => {
    sourceCalls.push(path);
    return { configured: false, provider: "off" };
  });
  assert.deepEqual(sourceCalls, ["/api/v1/git/source"]);
  assert.equal(unconfigured.problem, "unconfigured");
  assert.deepEqual(unconfigured.items, []);

  const listed = await fetchGitCatalog(async (path) => {
    if (path === "/api/v1/git/source") {
      return { configured: true, provider: "gitea" };
    }
    if (path === "/api/v1/git/repositories") {
      return { items: [{ full_name: "ada/demo" }] };
    }
    throw new Error(path);
  });
  assert.equal(listed.problem, null);
  assert.equal(listed.items[0].full_name, "ada/demo");
  assert.equal(repositoryFieldState(listed, []).mode, "checklist");

  const missing = await fetchGitCatalog(async (path) => {
    if (path.endsWith("/repositories")) {
      const error = new Error("无法读取仓库");
      error.status = 500;
      throw error;
    }
    return { configured: true, provider: "gitea" };
  });
  assert.equal(missing.problem, "list");
  assert.equal(missing.source.configured, true);
  assert.equal(repositoryFieldState(missing, "").mode, "manual");

  const quiet = await fetchGitCatalog(async (path) => {
    if (path.endsWith("/repositories")) {
      const error = new Error("未配置");
      error.status = 503;
      throw error;
    }
    return { configured: true, provider: "gitea" };
  });
  assert.equal(quiet.problem, "unconfigured");

  const denied = await fetchGitCatalog(async () => {
    const error = new Error("没有权限");
    error.status = 403;
    throw error;
  });
  assert.equal(denied.problem, "forbidden");
  assert.equal(denied.source, null);
});

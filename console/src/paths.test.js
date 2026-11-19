import assert from "node:assert/strict";
import { test } from "node:test";

import { apiPath, gitBranchesPath, isUuid } from "./paths.js";

test("apiPath encodes each interpolation", () => {
  assert.equal(apiPath`/p/${"../"}`, "/p/..%2F");
  assert.equal(apiPath`/p/${"%2F"}`, "/p/%252F");
  assert.equal(apiPath`/p/${"/"}`, "/p/%2F");
  assert.equal(apiPath`/p/${"?"}`, "/p/%3F");
  assert.equal(apiPath`/p/${"#"}`, "/p/%23");
  assert.equal(apiPath`/p/${"+"}`, "/p/%2B");
  assert.equal(apiPath`/api/v1/skills/${"one"}/reject`, "/api/v1/skills/one/reject");
  assert.equal(apiPath`/a/${"one"}/b/${"two"}`, "/a/one/b/two");
});

test("git branch queries encode the repository so slashes stay in the value", () => {
  const nested = gitBranchesPath("group/sub/app");
  assert.equal(nested, "/api/v1/git/branches?repository=group%2Fsub%2Fapp");
  assert.equal(nested.split("?")[1].includes("/"), false);
  assert.equal(gitBranchesPath("ada/demo?x#y"), "/api/v1/git/branches?repository=ada%2Fdemo%3Fx%23y");
  assert.equal(gitBranchesPath("ada/demo+1"), "/api/v1/git/branches?repository=ada%2Fdemo%2B1");
});

test("isUuid accepts a canonical uuid and rejects lookalikes", () => {
  assert.equal(isUuid("123e4567-e89b-12d3-a456-426614174000"), true);
  assert.equal(isUuid("123E4567-E89B-12D3-A456-426614174000"), true);
  assert.equal(isUuid("not-a-uuid"), false);
  assert.equal(isUuid("123e4567-e89b-12d3-a456-42661417400"), false);
  assert.equal(isUuid("../123e4567-e89b-12d3-a456-426614174000"), false);
  assert.equal(isUuid("123e4567-e89b-12d3-a456-426614174000/log"), false);
  assert.equal(isUuid(""), false);
  assert.equal(isUuid(null), false);
});

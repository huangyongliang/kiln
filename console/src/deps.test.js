import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

function newerThan(version, floor) {
  const left = version.split(".").map((part) => Number.parseInt(part, 10));
  const right = floor.split(".").map((part) => Number.parseInt(part, 10));
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const a = left[index] || 0;
    const b = right[index] || 0;
    if (a !== b) {
      return a > b;
    }
  }
  return false;
}

test("vite and esbuild dev dependencies are past the audited ranges", () => {
  const lock = JSON.parse(
    readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../package-lock.json"), "utf8"),
  );
  const versions = (name) =>
    Object.entries(lock.packages)
      .filter(([key]) => key === `node_modules/${name}` || key.endsWith(`/node_modules/${name}`))
      .map(([, value]) => value.version);
  const vite = versions("vite");
  const esbuild = versions("esbuild");
  assert.ok(vite.length > 0, "vite missing from the lockfile");
  assert.ok(esbuild.length > 0, "esbuild missing from the lockfile");
  for (const version of vite) {
    assert.equal(newerThan(version, "6.4.3"), true, version);
  }
  for (const version of esbuild) {
    assert.equal(newerThan(version, "0.24.2"), true, version);
  }
});

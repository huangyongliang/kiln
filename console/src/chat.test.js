import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CHAT_MESSAGE_LIMIT,
  CHAT_THREAD_KEY,
  chatRequestMessages,
  readChatThread,
  trimChatMessages,
  writeChatThread,
} from "./chat.js";

const SKILL_ID = "11111111-1111-4111-8111-111111111111";

test("a request keeps successful turns and drops failed bubbles and actions", () => {
  const messages = chatRequestMessages(
    [
      { role: "system", content: "规则" },
      { role: "user", content: "第一问" },
      { role: "user", content: "发送失败", failed: true },
      {
        role: "assistant",
        content: "第一答",
        actions: [{ type: "skill_drafted", id: SKILL_ID, name: "流程" }],
      },
      { role: "note", content: "忽略" },
      { role: "assistant", content: "", actions: [] },
    ],
    "第二问",
  );
  assert.deepEqual(messages, [
    { role: "system", content: "规则" },
    { role: "user", content: "第一问" },
    { role: "assistant", content: "第一答" },
    { role: "user", content: "第二问" },
  ]);
  assert.equal(Object.hasOwn(messages[2], "actions"), false);
  assert.equal(Object.hasOwn(messages[1], "failed"), false);
});

test("history is truncated to 32 and a system message is kept", () => {
  const history = [{ role: "system", content: "规则" }];
  for (let index = 0; index < 40; index += 1) {
    history.push({ role: "user", content: `问${index}` });
    history.push({ role: "assistant", content: `答${index}` });
  }
  history.push({ role: "user", content: "这条失败了", failed: true });
  const messages = chatRequestMessages(history, "最新");
  assert.equal(messages.length, CHAT_MESSAGE_LIMIT);
  assert.equal(CHAT_MESSAGE_LIMIT, 32);
  assert.deepEqual(messages[0], { role: "system", content: "规则" });
  assert.deepEqual(messages.at(-1), { role: "user", content: "最新" });
  assert.equal(messages.some((item) => item.content === "问0"), false);
  assert.equal(messages.some((item) => item.content === "这条失败了"), false);
  assert.equal(messages.some((item) => item.failed || item.actions), false);
  const withoutSystem = messages.slice(1).map((item) => item.content);
  const tail = [];
  for (let index = 0; index < 40; index += 1) {
    tail.push(`问${index}`, `答${index}`);
  }
  tail.push("最新");
  assert.deepEqual(withoutSystem, tail.slice(-(CHAT_MESSAGE_LIMIT - 1)));
});

test("without a system message the oldest turns are dropped", () => {
  const history = [];
  for (let index = 0; index < 40; index += 1) {
    history.push({ role: "user", content: `u${index}` });
  }
  const messages = trimChatMessages(
    [...history, { role: "user", content: "newest" }],
    32,
  );
  assert.equal(messages.length, 32);
  assert.equal(messages[0].content, "u9");
  assert.equal(messages.at(-1).content, "newest");
});

test("the page thread round-trips in session storage without action noise", () => {
  const storage = memoryStorage();
  writeChatThread(storage, [
    { role: "user", content: "问" },
    { role: "user", content: "失败", failed: true },
    {
      role: "assistant",
      content: "答",
      actions: [
        { type: "skill_drafted", id: SKILL_ID, name: "流程" },
        { type: "tool", id: SKILL_ID, name: "隐藏" },
      ],
    },
    { role: "assistant", content: "" },
  ]);
  assert.deepEqual(readChatThread(storage), [
    { role: "user", content: "问" },
    { role: "user", content: "失败", failed: true },
    {
      role: "assistant",
      content: "答",
      actions: [{ type: "skill_drafted", id: SKILL_ID, name: "流程" }],
    },
  ]);
  assert.equal(storage.getItem(CHAT_THREAD_KEY) != null, true);
  storage.setItem(CHAT_THREAD_KEY, "{");
  assert.deepEqual(readChatThread(storage), []);
});

function memoryStorage() {
  const data = new Map();
  return {
    getItem(key) {
      return data.has(key) ? data.get(key) : null;
    },
    setItem(key, value) {
      data.set(key, String(value));
    },
  };
}

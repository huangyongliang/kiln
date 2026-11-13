import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CHAT_MESSAGE_LIMIT,
  CHAT_MODEL_KEY,
  CHAT_THREAD_KEY,
  EMPTY_CATALOG_TIP,
  STALE_MODEL_TIP,
  chatModelState,
  chatRequestBody,
  chatRequestMessages,
  readChatModel,
  readChatThread,
  showModelPicker,
  trimChatMessages,
  writeChatModel,
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
        { type: "skill_updated", id: SKILL_ID, name: "流程" },
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
      actions: [
        { type: "skill_drafted", id: SKILL_ID, name: "流程" },
        { type: "skill_updated", id: SKILL_ID, name: "流程" },
      ],
    },
  ]);
  assert.equal(storage.getItem(CHAT_THREAD_KEY) != null, true);
  storage.setItem(CHAT_THREAD_KEY, "{");
  assert.deepEqual(readChatThread(storage), []);
});

test("off and mock do not show the picker", () => {
  assert.equal(showModelPicker({ provider: "off", models: ["model-a"] }), false);
  assert.equal(showModelPicker({ provider: "mock", models: ["model-a"] }), false);
  assert.equal(showModelPicker({ provider: "openai", models: [] }), false);
  assert.equal(showModelPicker({ provider: "openai", models: ["model-a"] }), true);
  assert.equal(showModelPicker(null), false);
  const mock = chatModelState(
    { provider: "mock", models: ["model-a"], default_model: "model-a" },
    "model-a",
  );
  assert.equal(mock.showPicker, false);
  assert.equal(mock.model, "");
  assert.equal(mock.notice, "");
  const off = chatModelState({ provider: "off", models: [], default_model: null }, "model-a");
  assert.equal(off.showPicker, false);
  assert.equal(off.notice, "");
});

test("an empty openai catalog asks for configuration and does not invent a model", () => {
  const state = chatModelState(
    { provider: "openai", models: [], default_model: null },
    "typed-by-hand",
  );
  assert.equal(state.showPicker, false);
  assert.equal(state.model, "");
  assert.equal(state.fellBack, false);
  assert.equal(state.notice, EMPTY_CATALOG_TIP);
  const body = chatRequestBody([{ role: "user", content: "问" }], "下一问", state.model);
  assert.equal(Object.hasOwn(body, "model"), false);
});

test("the selected model is stored, sent, and a later change leaves history alone", () => {
  const storage = memoryStorage();
  const provider = {
    provider: "openai",
    models: ["model-a", "model-b"],
    default_model: "model-a",
  };
  writeChatThread(storage, [
    { role: "user", content: "问", model: "should-drop" },
    { role: "assistant", content: "答", model: "model-a" },
  ]);
  const threadBefore = storage.getItem(CHAT_THREAD_KEY);
  writeChatModel(storage, "model-b");
  assert.equal(readChatModel(storage), "model-b");
  assert.equal(storage.getItem(CHAT_MODEL_KEY), "model-b");
  const picked = chatModelState(provider, readChatModel(storage));
  assert.equal(picked.showPicker, true);
  assert.equal(picked.model, "model-b");
  assert.equal(picked.fellBack, false);
  const first = chatRequestBody(readChatThread(storage), "下一问", picked.model);
  writeChatModel(storage, "model-a");
  const second = chatRequestBody(readChatThread(storage), "再问", readChatModel(storage));
  assert.equal(first.model, "model-b");
  assert.equal(second.model, "model-a");
  assert.deepEqual(first.messages, [
    { role: "user", content: "问" },
    { role: "assistant", content: "答" },
    { role: "user", content: "下一问" },
  ]);
  assert.equal(first.messages.some((item) => Object.hasOwn(item, "model")), false);
  assert.equal(second.messages.some((item) => Object.hasOwn(item, "model")), false);
  assert.equal(storage.getItem(CHAT_THREAD_KEY), threadBefore);
  assert.deepEqual(readChatThread(storage)[1], {
    role: "assistant",
    content: "答",
    model: "model-a",
  });
});

test("a stale selection falls back to the default and stays sendable", () => {
  const storage = memoryStorage();
  const provider = {
    provider: "openai",
    models: ["model-a", "model-b"],
    default_model: "model-b",
  };
  writeChatModel(storage, "retired-model");
  const state = chatModelState(provider, readChatModel(storage));
  assert.equal(state.showPicker, true);
  assert.equal(state.fellBack, true);
  assert.equal(state.model, "model-b");
  assert.equal(state.notice, STALE_MODEL_TIP);
  writeChatModel(storage, state.model);
  assert.equal(readChatModel(storage), "model-b");
  const body = chatRequestBody([], "还能发", state.model);
  assert.equal(body.model, "model-b");
  assert.deepEqual(body.messages, [{ role: "user", content: "还能发" }]);
  const first = chatModelState(
    { provider: "openai", models: ["first-model", "second-model"], default_model: null },
    "retired-model",
  );
  assert.equal(first.model, "first-model");
  assert.equal(first.fellBack, true);
  const fresh = chatModelState(provider, "");
  assert.equal(fresh.model, "model-b");
  assert.equal(fresh.fellBack, false);
  assert.equal(fresh.notice, "");
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
    removeItem(key) {
      data.delete(key);
    },
  };
}

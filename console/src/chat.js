/**
 * Assemble one chat request from the page thread.
 * Failed bubbles, action links, and the assistant model stay on the page.
 * They are not sent. The selected model id lives in its own sessionStorage
 * key and is not mixed into the message array.
 */

import { isUuid } from "./paths.js";

export const CHAT_MESSAGE_LIMIT = 32;
export const CHAT_THREAD_KEY = "kiln.chat.thread";
export const CHAT_MODEL_KEY = "kiln.chat.model";
export const STALE_MODEL_TIP = "上次选择的模型已不在允许列表中，已改用默认模型。";
export const EMPTY_CATALOG_TIP = "请运营配置 LLM_MODEL 或 LLM_MODELS。";

const MODEL_ID_MAX = 128;

const ROLES = new Set(["system", "user", "assistant"]);
const ACTION_TYPES = new Set(["skill_drafted", "skill_updated", "build_started"]);

export function chatRequestBody(history, content, model) {
  const body = { messages: chatRequestMessages(history, content) };
  if (typeof model === "string" && model.length > 0) {
    body.model = model;
  }
  return body;
}

/**
 * Move a non-empty composer into the thread and clear it.
 * The caller does this before POST /llm/chat. The returned prompt is empty,
 * so a later failure cannot put this text back by reusing the result.
 */
export function takeUserMessage(prompt, messages) {
  const content = String(prompt ?? "").trim();
  if (!content) {
    return null;
  }
  const thread = Array.isArray(messages) ? messages : [];
  return {
    content,
    prompt: "",
    messages: [...thread, { role: "user", content, failed: false }],
  };
}

/**
 * Mark the latest user bubble as failed.
 * `prompt` is the composer as it stands now: still empty, or text typed
 * while the request was in flight. It is returned unchanged.
 */
export function failOutgoingMessage(messages, prompt) {
  const thread = Array.isArray(messages) ? messages.slice() : [];
  const last = thread[thread.length - 1];
  if (last && last.role === "user") {
    thread[thread.length - 1] = { ...last, failed: true };
  }
  return { messages: thread, prompt };
}

export function showModelPicker(provider) {
  return provider?.provider === "openai" && catalogIds(provider).length > 0;
}

/**
 * Decide the picker for this tab.
 * `saved` is the sessionStorage id, kept separate from the message array.
 * A saved id that is no longer in the catalog falls back to default_model,
 * or to the first catalog item, and stays sendable. off and mock never
 * show a picker, even if a models list is present. An empty openai catalog
 * does not offer a free-text model id.
 */
export function chatModelState(provider, saved) {
  const models = catalogIds(provider);
  const showPicker = provider?.provider === "openai" && models.length > 0;
  if (!showPicker) {
    return {
      showPicker: false,
      model: "",
      fellBack: false,
      notice: provider?.provider === "openai" ? EMPTY_CATALOG_TIP : "",
    };
  }
  const savedId = typeof saved === "string" ? saved : "";
  if (savedId && models.includes(savedId)) {
    return { showPicker: true, model: savedId, fellBack: false, notice: "" };
  }
  const preferred = typeof provider.default_model === "string" ? provider.default_model : "";
  const model = preferred && models.includes(preferred) ? preferred : models[0];
  const fellBack = savedId.length > 0;
  return {
    showPicker: true,
    model,
    fellBack,
    notice: fellBack ? STALE_MODEL_TIP : "",
  };
}

export function readChatModel(storage) {
  if (!storage) {
    return "";
  }
  try {
    const raw = storage.getItem(CHAT_MODEL_KEY);
    return typeof raw === "string" ? raw : "";
  } catch {
    return "";
  }
}

export function writeChatModel(storage, model) {
  if (!storage) {
    return;
  }
  try {
    if (typeof model === "string" && model.length > 0) {
      storage.setItem(CHAT_MODEL_KEY, model);
    } else {
      storage.removeItem(CHAT_MODEL_KEY);
    }
  } catch {
    // The page still sends when the browser refuses to store the selection.
  }
}

export function chatRequestMessages(history, content) {
  const prior = [];
  for (const item of history || []) {
    if (!item || item.failed || !ROLES.has(item.role)) {
      continue;
    }
    if (typeof item.content !== "string" || item.content.length === 0) {
      continue;
    }
    prior.push({ role: item.role, content: item.content });
  }
  prior.push({ role: "user", content });
  return trimChatMessages(prior, CHAT_MESSAGE_LIMIT);
}

export function trimChatMessages(messages, limit = CHAT_MESSAGE_LIMIT) {
  const copy = messages.map((item) => ({ role: item.role, content: item.content }));
  if (copy.length <= limit) {
    return copy;
  }
  const systemIndex = copy.findIndex((item) => item.role === "system");
  const drop = new Set();
  let extra = copy.length - limit;
  for (let index = 0; index < copy.length && extra > 0; index += 1) {
    if (index === systemIndex) {
      continue;
    }
    drop.add(index);
    extra -= 1;
  }
  return copy.filter((_, index) => !drop.has(index));
}

export function readChatThread(storage) {
  if (!storage) {
    return [];
  }
  try {
    const raw = storage.getItem(CHAT_THREAD_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.map(storedMessage).filter(Boolean);
  } catch {
    return [];
  }
}

export function writeChatThread(storage, messages) {
  if (!storage) {
    return;
  }
  try {
    const thread = (messages || []).map(storedMessage).filter(Boolean);
    storage.setItem(CHAT_THREAD_KEY, JSON.stringify(thread));
  } catch {
    // The page still works when the browser refuses to store the thread.
  }
}

function storedMessage(item) {
  if (!item || typeof item !== "object" || !ROLES.has(item.role)) {
    return null;
  }
  if (typeof item.content !== "string" || item.content.length === 0) {
    return null;
  }
  const stored = { role: item.role, content: item.content };
  if (
    item.role === "assistant" &&
    typeof item.model === "string" &&
    item.model.length > 0 &&
    item.model.length <= MODEL_ID_MAX
  ) {
    stored.model = item.model;
  }
  if (item.failed === true) {
    stored.failed = true;
  }
  if (Array.isArray(item.actions)) {
    const actions = item.actions.map(storedAction).filter(Boolean);
    if (actions.length) {
      stored.actions = actions;
    }
  }
  return stored;
}

function catalogIds(provider) {
  if (!provider || !Array.isArray(provider.models)) {
    return [];
  }
  return provider.models.filter((id) => typeof id === "string" && id.length > 0);
}

function storedAction(item) {
  if (!item || !ACTION_TYPES.has(item.type) || !isUuid(item.id)) {
    return null;
  }
  if (typeof item.name !== "string" || item.name.length === 0) {
    return null;
  }
  return { type: item.type, id: item.id, name: item.name };
}

/**
 * Assemble one chat request from the page thread.
 * Failed bubbles and action links stay on the page. They are not sent.
 */

import { isUuid } from "./paths.js";

export const CHAT_MESSAGE_LIMIT = 32;
export const CHAT_THREAD_KEY = "kiln.chat.thread";

const ROLES = new Set(["system", "user", "assistant"]);
const ACTION_TYPES = new Set(["skill_drafted", "skill_updated", "build_started"]);

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

function storedAction(item) {
  if (!item || !ACTION_TYPES.has(item.type) || !isUuid(item.id)) {
    return null;
  }
  if (typeof item.name !== "string" || item.name.length === 0) {
    return null;
  }
  return { type: item.type, id: item.id, name: item.name };
}

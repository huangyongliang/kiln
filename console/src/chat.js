/**
 * Assemble one chat request from the page thread.
 * Failed bubbles, action links, and the assistant model stay on the page.
 * They are not sent. Conversations, drafts, and the selected model live in
 * localStorage under kiln.chat.*. The open conversation id is per tab.
 */

import { isUuid } from "./paths.js";

export const CHAT_MESSAGE_LIMIT = 32;
export const CHAT_THREAD_KEY = "kiln.chat.thread";
export const CHAT_MODEL_KEY = "kiln.chat.model";
export const CHAT_SCHEMA_VERSION_KEY = "kiln.chat.schemaVersion";
export const CHAT_CONVERSATIONS_KEY = "kiln.chat.conversations";
export const CHAT_ACTIVE_ID_KEY = "kiln.chat.activeId";
export const CHAT_SCHEMA_VERSION = 1;
export const CHAT_CONVERSATION_LIMIT = 50;
export const NEW_CONVERSATION_TITLE = "新对话";
export const STALE_MODEL_TIP = "上次选择的模型已不在允许列表中，已改用默认模型。";
export const EMPTY_CATALOG_TIP =
  "当前没有可用的模型。请管理员在模型配置中分配，或检查环境变量 LLM_MODEL / LLM_MODELS。";
export const MODEL_NOT_ALLOWED = "模型不在允许的模型列表中。";
export const NO_MODELS_FOR_ACCOUNT = "当前账号没有可用的模型。请联系管理员在模型配置中分配。";
export const NEW_CHAT_WARNING =
  "新建对话会清除本页消息和未发送内容，无法恢复。已创建的 Skill 和已发起的构建仍会保留；停止等待不能撤销服务器已执行或仍在执行的操作。";
export const DELETE_CHAT_WARNING =
  "删除后无法恢复。这段对话只保存在本机浏览器中。已创建的 Skill 和已发起的构建仍会保留。";
export const CONVERSATION_LIMIT_TIP = "本机最多保存 50 段对话。请先删除旧对话后再新建。";
export const CONVERSATION_SAVE_TIP = "本机暂无法保存更多对话。已有对话仍保留在页面上。";
export const INTERRUPTED_MARK = "结果未知，请求已中断";
export const INTERRUPTED_HINT = "请先查看 Skill 和构建记录，再手工继续。";

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
    const next = { ...last, failed: true };
    delete next.pending;
    thread[thread.length - 1] = next;
  }
  return { messages: thread, prompt };
}

export function newChatNeedsConfirm({ messages, draft, requestActive }) {
  const hasThread = Array.isArray(messages) && messages.length > 0;
  const hasDraft = typeof draft === "string" && draft.trim().length > 0;
  return hasThread || hasDraft || requestActive === true;
}

export function isDisallowedModelError(caught) {
  return isDetail(caught, MODEL_NOT_ALLOWED);
}

export function isNoModelsError(caught) {
  return isDetail(caught, NO_MODELS_FOR_ACCOUNT);
}

export function shouldRefetchProvider(caught) {
  return isDisallowedModelError(caught) || isNoModelsError(caught);
}

function isDetail(caught, detail) {
  if (!caught || caught.status !== 400) {
    return false;
  }
  return caught.message === detail || caught.detail === detail;
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
    return false;
  }
  try {
    if (typeof model === "string" && model.length > 0) {
      storage.setItem(CHAT_MODEL_KEY, model);
    } else {
      storage.removeItem(CHAT_MODEL_KEY);
    }
    return true;
  } catch {
    // The page still sends when the browser refuses to store the selection.
    return false;
  }
}

export function clearChatStorage(storage) {
  if (!storage || typeof storage.removeItem !== "function") {
    return;
  }
  const keys = new Set([
    CHAT_THREAD_KEY,
    CHAT_MODEL_KEY,
    CHAT_SCHEMA_VERSION_KEY,
    CHAT_CONVERSATIONS_KEY,
    CHAT_ACTIVE_ID_KEY,
  ]);
  if (typeof storage.length === "number" && typeof storage.key === "function") {
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (typeof key === "string" && key.startsWith("kiln.chat.")) {
        keys.add(key);
      }
    }
  }
  for (const key of keys) {
    try {
      storage.removeItem(key);
    } catch {
      // Logout still finishes when one key cannot be removed.
    }
  }
}

// Enter sends. Shift+Enter inserts a newline. An IME confirmation must not send.
export function composerKeyAction(event) {
  if (!event || event.isComposing || event.key === "Process" || event.keyCode === 229) {
    return "insert";
  }
  if (event.key === "Enter" && !event.shiftKey) {
    return "send";
  }
  return "insert";
}

export function chatRequestMessages(history, content) {
  const prior = [];
  for (const item of history || []) {
    if (!item || item.failed || item.pending || item.interrupted || !ROLES.has(item.role)) {
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
  return readChatThreadState(storage).messages;
}

export function readChatThreadState(storage) {
  if (!storage) {
    return { messages: [], sawPending: false };
  }
  try {
    const raw = storage.getItem(CHAT_THREAD_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) {
      return { messages: [], sawPending: false };
    }
    return normalizeLoadedThread(parsed.map(storedMessage).filter(Boolean));
  } catch {
    return { messages: [], sawPending: false };
  }
}

export function normalizeLoadedThread(messages) {
  let sawPending = false;
  const next = [];
  for (const item of messages || []) {
    if (!item || typeof item !== "object") {
      continue;
    }
    if (item.pending === true) {
      sawPending = true;
      const copy = { ...item };
      delete copy.pending;
      copy.interrupted = true;
      next.push(copy);
      continue;
    }
    next.push(item);
  }
  return { messages: next, sawPending };
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
  if (!item || typeof item !== "object" || !ROLES.has(item.role) || item.role === "system") {
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
  if (item.interrupted === true) {
    stored.interrupted = true;
  } else if (item.pending === true) {
    stored.pending = true;
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

const TITLE_LIMIT = 40;

export function conversationTitleFromContent(content) {
  const collapsed = String(content ?? "").trim().replace(/\s+/gu, " ");
  if (!collapsed) {
    return NEW_CONVERSATION_TITLE;
  }
  const chars = Array.from(collapsed);
  if (chars.length <= TITLE_LIMIT) {
    return collapsed;
  }
  return `${chars.slice(0, TITLE_LIMIT - 1).join("")}…`;
}

export function newConversationId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx".replace(/[x]/g, () =>
    Math.floor(Math.random() * 16).toString(16),
  );
}

export function createConversationRecord({ id, messages = [], draft = "", now = Date.now() } = {}) {
  const titled = titleFromMessages(messages);
  return {
    id: typeof id === "string" && id ? id : newConversationId(),
    title: titled.title,
    titleLocked: titled.titleLocked,
    createdAt: now,
    updatedAt: now,
    draft: typeof draft === "string" ? draft : "",
    messages: messages.map(storedMessage).filter(Boolean),
  };
}

export function readActiveConversationId(storage) {
  if (!storage) {
    return "";
  }
  try {
    const raw = storage.getItem(CHAT_ACTIVE_ID_KEY);
    return typeof raw === "string" ? raw : "";
  } catch {
    return "";
  }
}

export function writeActiveConversationId(storage, id) {
  if (!storage) {
    return false;
  }
  try {
    if (typeof id === "string" && id) {
      storage.setItem(CHAT_ACTIVE_ID_KEY, id);
    } else {
      storage.removeItem(CHAT_ACTIVE_ID_KEY);
    }
    return true;
  } catch {
    return false;
  }
}

export function conversationsFromSnapshot(raw) {
  try {
    const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
    if (!Array.isArray(parsed)) {
      return null;
    }
    return parsed.map(normalizeStoredConversation).filter(Boolean);
  } catch {
    return null;
  }
}

export function readStoredConversations(storage) {
  if (!storage) {
    return { present: false, valid: false, conversations: [] };
  }
  let raw = null;
  try {
    raw = storage.getItem(CHAT_CONVERSATIONS_KEY);
  } catch {
    return { present: false, valid: false, conversations: [] };
  }
  if (raw == null) {
    return { present: false, valid: false, conversations: [] };
  }
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return { present: true, valid: false, conversations: [] };
    }
    return {
      present: true,
      valid: true,
      conversations: parsed.map(normalizeStoredConversation).filter(Boolean),
    };
  } catch {
    return { present: true, valid: false, conversations: [] };
  }
}

export function hasChatSchema(storage) {
  if (!storage) {
    return false;
  }
  try {
    return storage.getItem(CHAT_SCHEMA_VERSION_KEY) === String(CHAT_SCHEMA_VERSION);
  } catch {
    return false;
  }
}

export function writeConversationStore(storage, conversations) {
  if (!storage) {
    return false;
  }
  let encoded = "";
  try {
    encoded = JSON.stringify((conversations || []).map(storedConversation).filter(Boolean));
  } catch {
    return false;
  }
  try {
    storage.setItem(CHAT_CONVERSATIONS_KEY, encoded);
  } catch {
    return false;
  }
  try {
    storage.setItem(CHAT_SCHEMA_VERSION_KEY, String(CHAT_SCHEMA_VERSION));
  } catch {
    // The conversation array is already stored. Schema can be repaired later.
  }
  return true;
}

/**
 * Move the legacy single thread into schema v1.
 * v1 is written before the old thread key is removed. A later load does not
 * import that key again once a schema or a conversation array exists.
 */
export function migrateChatStorage(localStore, sessionStore, now = Date.now()) {
  migrateChatModel(localStore, sessionStore);
  const schema = hasChatSchema(localStore);
  const stored = readStoredConversations(localStore);
  if (schema || stored.valid) {
    removeLegacyThread(sessionStore);
    if (stored.valid && !schema) {
      try {
        localStore?.setItem(CHAT_SCHEMA_VERSION_KEY, String(CHAT_SCHEMA_VERSION));
      } catch {
        // The array is the source of truth until schema can be written.
      }
    }
    return {
      conversations: stored.valid ? stored.conversations : [],
      saveFailed: false,
    };
  }
  let raw = null;
  let readable = false;
  try {
    raw = sessionStore ? sessionStore.getItem(CHAT_THREAD_KEY) : null;
    readable = true;
  } catch {
    readable = false;
  }
  if (!readable || raw == null) {
    return { conversations: [], saveFailed: false };
  }
  const messages = messagesFromLegacy(raw);
  const conversation = createConversationRecord({ messages, now });
  const saved = writeConversationStore(localStore, [conversation]);
  if (saved) {
    removeLegacyThread(sessionStore);
  }
  return { conversations: [conversation], saveFailed: !saved };
}

export function migrateChatModel(localStore, sessionStore) {
  const localModel = readChatModel(localStore);
  const sessionModel = readChatModel(sessionStore);
  if (!localModel && sessionModel) {
    const saved = writeChatModel(localStore, sessionModel);
    if (!saved) {
      return sessionModel;
    }
  }
  if (sessionModel && sessionStore) {
    try {
      sessionStore.removeItem(CHAT_MODEL_KEY);
    } catch {
      // The local copy is already preferred.
    }
  }
  return localModel || sessionModel || "";
}

function titleFromMessages(messages) {
  const firstUser = (messages || []).find(
    (item) => item && item.role === "user" && typeof item.content === "string" && item.content.trim(),
  );
  if (!firstUser) {
    return { title: NEW_CONVERSATION_TITLE, titleLocked: false };
  }
  return { title: conversationTitleFromContent(firstUser.content), titleLocked: true };
}

function messagesFromLegacy(raw) {
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return normalizeLoadedThread(parsed.map(storedMessage).filter(Boolean)).messages;
  } catch {
    return [];
  }
}

function normalizeStoredConversation(item) {
  if (!item || typeof item !== "object" || typeof item.id !== "string" || !item.id) {
    return null;
  }
  const loaded = normalizeLoadedThread(
    (Array.isArray(item.messages) ? item.messages : []).map(storedMessage).filter(Boolean),
  );
  const titled = titleFromMessages(loaded.messages);
  const title =
    item.titleLocked === true && typeof item.title === "string" && item.title
      ? item.title
      : titled.title;
  return {
    id: item.id,
    title,
    titleLocked: item.titleLocked === true || titled.titleLocked === true,
    createdAt: Number.isFinite(item.createdAt) ? item.createdAt : 0,
    updatedAt: Number.isFinite(item.updatedAt) ? item.updatedAt : 0,
    draft: typeof item.draft === "string" ? item.draft : "",
    messages: loaded.messages,
    sawPending: loaded.sawPending,
  };
}

function storedConversation(item) {
  if (!item || typeof item.id !== "string" || !item.id) {
    return null;
  }
  const messages = (item.messages || []).map(storedMessage).filter(Boolean);
  return {
    id: item.id,
    title: typeof item.title === "string" && item.title ? item.title : NEW_CONVERSATION_TITLE,
    titleLocked: item.titleLocked === true,
    createdAt: Number.isFinite(item.createdAt) ? item.createdAt : 0,
    updatedAt: Number.isFinite(item.updatedAt) ? item.updatedAt : 0,
    draft: typeof item.draft === "string" ? item.draft : "",
    messages,
  };
}

function removeLegacyThread(storage) {
  if (!storage) {
    return;
  }
  try {
    storage.removeItem(CHAT_THREAD_KEY);
  } catch {
    // A later successful v1 write can remove it.
  }
}

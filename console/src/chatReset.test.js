import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { createServer, request as httpRequest } from "node:http";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { afterEach, beforeEach, test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

function memoryStorage() {
  const data = new Map();
  return {
    get length() {
      return data.size;
    },
    key(index) {
      return [...data.keys()][index] ?? null;
    },
    getItem(key) {
      return data.has(key) ? data.get(key) : null;
    },
    setItem(key, value) {
      data.set(key, String(value));
    },
    removeItem(key) {
      data.delete(key);
    },
    clear() {
      data.clear();
    },
  };
}

const sessionStorage = memoryStorage();
const localStorage = memoryStorage();
const assigned = [];
const location = {
  pathname: "/chat",
  reloads: 0,
  assign(url) {
    assigned.push(String(url));
    this.pathname = String(url);
  },
  reload() {
    this.reloads += 1;
  },
};

globalThis.sessionStorage = sessionStorage;
globalThis.localStorage = localStorage;

class MiniNode {
  constructor() {
    this.childNodes = [];
    this.parentNode = null;
    this.ownerDocument = null;
    this.nodeValue = "";
  }

  get nextSibling() {
    if (!this.parentNode) {
      return null;
    }
    const index = this.parentNode.childNodes.indexOf(this);
    return this.parentNode.childNodes[index + 1] || null;
  }

  get textContent() {
    if (this.nodeType === 3 || this.nodeType === 8) {
      return String(this.nodeValue ?? "");
    }
    return this.childNodes.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this.childNodes = [];
    if (value == null || value === "") {
      return;
    }
    const text = new MiniText(String(value));
    text.ownerDocument = this.ownerDocument;
    this.appendChild(text);
  }

  appendChild(child) {
    return this.insertBefore(child, null);
  }

  insertBefore(child, anchor) {
    if (child.parentNode) {
      child.parentNode.removeChild(child);
    }
    child.parentNode = this;
    child.ownerDocument = this.ownerDocument || child.ownerDocument;
    const index = anchor ? this.childNodes.indexOf(anchor) : -1;
    if (index >= 0) {
      this.childNodes.splice(index, 0, child);
    } else {
      this.childNodes.push(child);
    }
    return child;
  }

  removeChild(child) {
    const index = this.childNodes.indexOf(child);
    if (index >= 0) {
      this.childNodes.splice(index, 1);
      child.parentNode = null;
    }
    return child;
  }
}

class MiniText extends MiniNode {
  constructor(text) {
    super();
    this.nodeType = 3;
    this.nodeValue = text;
  }
}

class MiniComment extends MiniNode {
  constructor(text) {
    super();
    this.nodeType = 8;
    this.nodeValue = text;
  }
}

class MiniElement extends MiniNode {
  constructor(tag) {
    super();
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.namespaceURI = null;
    this.attributes = new Map();
    this.className = "";
    this.domValue = "";
    this.selected = false;
    this.listeners = new Map();
    this.style = new Proxy(
      { setProperty() {}, removeProperty() {}, getPropertyValue() { return ""; } },
      {
        set(target, key, value) {
          target[key] = value;
          return true;
        },
        get(target, key) {
          if (key === "setProperty") {
            return (name, value) => {
              target[name] = value;
            };
          }
          if (key === "removeProperty") {
            return (name) => {
              delete target[name];
            };
          }
          return target[key];
        },
      },
    );
    this.classList = {
      add: (...names) => {
        const current = new Set(this.className.split(/\s+/).filter(Boolean));
        for (const name of names) {
          current.add(name);
        }
        this.className = [...current].join(" ");
      },
      remove: (...names) => {
        const drop = new Set(names);
        this.className = this.className.split(/\s+/).filter((name) => name && !drop.has(name)).join(" ");
      },
    };
  }

  get type() {
    if (this.tagName === "TEXTAREA") {
      return "textarea";
    }
    return this.getAttribute("type") || "";
  }

  get defaultValue() {
    return this.domValue;
  }

  getRootNode() {
    return { activeElement: null };
  }

  get value() {
    return this.domValue;
  }

  set value(next) {
    this.domValue = next == null ? "" : String(next);
  }

  get options() {
    return this.querySelectorAll("option");
  }

  get selectedIndex() {
    return this.options.findIndex((option) => option.selected);
  }

  set selectedIndex(index) {
    this.options.forEach((option, optionIndex) => {
      option.selected = optionIndex === index;
    });
    const selected = this.options[index];
    if (selected) {
      this.domValue = selected.value;
    }
  }

  set innerHTML(value) {
    this.textContent = String(value ?? "").replace(/<[^>]*>/g, "");
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name === "class") {
      this.className = String(value);
    }
    if (name === "value") {
      this.domValue = String(value);
    }
  }

  getAttribute(name) {
    if (name === "class") {
      return this.className;
    }
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  removeAttribute(name) {
    this.attributes.delete(name);
    if (name === "class") {
      this.className = "";
    }
  }

  addEventListener(type, handler) {
    const list = this.listeners.get(type) || [];
    list.push(handler);
    this.listeners.set(type, list);
  }

  removeEventListener(type, handler) {
    const list = this.listeners.get(type) || [];
    this.listeners.set(type, list.filter((item) => item !== handler));
  }

  dispatchEvent(event) {
    event.target = this;
    for (const handler of this.listeners.get(event.type) || []) {
      handler(event);
    }
    return true;
  }

  click() {
    this.dispatchEvent({ type: "click", preventDefault() {}, stopPropagation() {} });
  }

  matches(selector) {
    if (selector.startsWith("#")) {
      return this.getAttribute("id") === selector.slice(1);
    }
    if (selector.startsWith(".")) {
      return this.className.split(/\s+/).includes(selector.slice(1));
    }
    const attribute = selector.match(/^\[([^\]=]+)(?:="([^"]*)")?\]$/);
    if (attribute) {
      const value = this.getAttribute(attribute[1]);
      return attribute[2] == null ? value != null : value === attribute[2];
    }
    return this.tagName === selector.toUpperCase();
  }

  querySelector(selector) {
    for (const child of this.childNodes) {
      if (child.nodeType === 1 && child.matches(selector)) {
        return child;
      }
      if (child.nodeType === 1) {
        const found = child.querySelector(selector);
        if (found) {
          return found;
        }
      }
    }
    return null;
  }

  querySelectorAll(selector) {
    const found = [];
    const visit = (node) => {
      for (const child of node.childNodes || []) {
        if (child.nodeType === 1 && child.matches(selector)) {
          found.push(child);
        }
        if (child.nodeType === 1) {
          visit(child);
        }
      }
    };
    visit(this);
    return found;
  }
}

const document = {
  nodeType: 9,
  body: null,
  documentElement: null,
  createElement(tag) {
    const element = new MiniElement(tag);
    element.ownerDocument = document;
    return element;
  },
  createElementNS(_namespace, tag) {
    return document.createElement(tag);
  },
  createTextNode(text) {
    const node = new MiniText(text);
    node.ownerDocument = document;
    return node;
  },
  createComment(text) {
    const node = new MiniComment(text);
    node.ownerDocument = document;
    return node;
  },
  querySelector(selector) {
    return document.body ? document.body.querySelector(selector) : null;
  },
  querySelectorAll(selector) {
    return document.body ? document.body.querySelectorAll(selector) : [];
  },
};
document.body = document.createElement("body");
document.documentElement = document.createElement("html");
document.documentElement.appendChild(document.body);
document.body.offsetHeight = 0;

globalThis.document = document;
globalThis.Node = MiniNode;
globalThis.Element = MiniElement;
globalThis.HTMLElement = MiniElement;
globalThis.SVGElement = MiniElement;
globalThis.Comment = MiniComment;
globalThis.Text = MiniText;
globalThis.DocumentFragment = MiniElement;
globalThis.window = {
  sessionStorage,
  localStorage,
  location,
  document,
  HTMLElement: MiniElement,
  SVGElement: MiniElement,
  Element: MiniElement,
  Node: MiniNode,
  navigator: { userAgent: "jsdom" },
  getComputedStyle() {
    return { getPropertyValue() { return ""; } };
  },
  requestAnimationFrame(callback) {
    const timer = setTimeout(callback, 0);
    timer.unref?.();
    return timer;
  },
  cancelAnimationFrame(id) {
    clearTimeout(id);
  },
  addEventListener() {},
  removeEventListener() {},
};
globalThis.getComputedStyle = globalThis.window.getComputedStyle;
if (typeof globalThis.Document === "undefined") {
  globalThis.Document = class Document {};
}
if (typeof globalThis.ShadowRoot === "undefined") {
  globalThis.ShadowRoot = class ShadowRoot {};
}
if (typeof globalThis.Event === "undefined") {
  globalThis.Event = class Event {
    constructor(type) {
      this.type = type;
    }
    preventDefault() {}
    stopPropagation() {}
  };
}
if (typeof globalThis.history === "undefined") {
  globalThis.history = {
    state: null,
    length: 1,
    pushState() {},
    replaceState() {},
    go() {},
    back() {},
    forward() {},
  };
}

const { createChatSession } = await import("./chatSession.js");
const {
  CHAT_MODEL_KEY,
  CHAT_THREAD_KEY,
  INTERRUPTED_HINT,
  INTERRUPTED_MARK,
  MODEL_NOT_ALLOWED,
  NEW_CHAT_WARNING,
  STALE_MODEL_TIP,
  readChatThread,
} = await import("./chat.js");
const {
  acceptLogin,
  advanceChatGeneration,
  currentAuthEpoch,
  flushChatPersist,
  flushHeldChatPersist,
  holdChatPersistForTests,
  logoutSession,
  openChatView,
  replaceApiToken,
  resetChatRuntimeForTests,
  scheduleChatPersist,
} = await import("./identity.js");
const { getJson, postJson, requestJson } = await import("./api.js");
const { accessReady, account, ensureAccess, perms, resetAccessForTests } = await import("./access.js");
const { readAccount, writeAccount } = await import("./session.js");
const { readApiToken, writeApiToken } = await import("./token.js");
const { ACTION_GATES, ROUTE_GATES, canDo, canSee } = await import("./permissions.js");
const { nextTick } = await import("vue");

const OPENAI = {
  provider: "openai",
  models: ["model-a", "model-b"],
  default_model: "model-a",
};
const QA = new Set([
  "agent.qa",
  "agent.draft_skill",
  "agent.build_artifact",
  "skill.use",
  "skill.read",
  "executor.run",
]);
const SKILL_ID = "11111111-1111-4111-8111-111111111111";

let calls = [];
const sessions = [];
const cookieJar = new Map();

function applySetCookie(header) {
  const parts = String(header).split(";").map((part) => part.trim());
  const [name, ...rest] = parts[0].split("=");
  const value = rest.join("=");
  const expired = parts.some((part) => /^max-age=0$/i.test(part));
  if (!name) {
    return;
  }
  if (expired || value === "") {
    cookieJar.delete(name);
  } else {
    cookieJar.set(name, value);
  }
}
const apps = [];
const require = createRequire(import.meta.url);
const { parse, compileScript } = require("@vue/compiler-sfc");
const compiledViews = new Map();
const viewBuildDir = resolve(dirname(fileURLToPath(import.meta.url)), "../node_modules/.kiln-chat-view-test");
mkdirSync(viewBuildDir, { recursive: true });

function abortError() {
  return new DOMException("The operation was aborted.", "AbortError");
}

function httpResponse({ status = 200, json = null, text = null } = {}) {
  const bodyText = text != null ? text : JSON.stringify(json);
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => bodyText,
  };
}

function installFetch() {
  calls = [];
  const fetchImpl = (url, options = {}) => {
    const call = {
      url: String(url),
      options,
      honorAbort: true,
      settled: false,
      taken: false,
    };
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
      resolve = res;
      reject = rej;
    });
    call.resolveWith = (payload) => {
      if (call.settled) {
        return;
      }
      call.settled = true;
      if (payload?.setCookie) {
        call.cookieApplied = call.options.signal?.aborted !== true;
        if (call.cookieApplied) {
          applySetCookie(payload.setCookie);
        }
      }
      resolve(httpResponse(payload));
    };
    call.rejectWith = (error) => {
      if (call.settled) {
        return;
      }
      call.settled = true;
      reject(error);
    };
    if (options.signal) {
      const fail = () => {
        if (call.honorAbort === false) {
          return;
        }
        call.rejectWith(abortError());
      };
      if (options.signal.aborted) {
        call.settled = true;
        return Promise.reject(abortError());
      }
      options.signal.addEventListener("abort", fail, { once: true });
    }
    calls.push(call);
    return promise;
  };
  globalThis.fetch = fetchImpl;
  window.fetch = fetchImpl;
}

function take(url, method = "GET") {
  const found = calls.findLast(
    (call) => call.url === url && !call.taken && (call.options.method || "GET") === method,
  );
  assert.ok(found, `missing ${method} ${url}; saw ${calls.map((call) => `${call.options.method || "GET"} ${call.url}`).join(" | ")}`);
  found.taken = true;
  return found;
}

function allowQa() {
  accessReady.value = true;
  perms.value = QA;
  account.value = { id: "user-1", username: "ada", display_name: "Ada", role_ids: ["qa"] };
  writeAccount(account.value);
}

function openSession(storage = sessionStorage) {
  const session = createChatSession({ storage });
  sessions.push(session);
  return session;
}

async function boot(session, provider = OPENAI, lists = { mine: [], usable: [] }) {
  const pending = session.load();
  const providerCall = take("/api/v1/llm/provider");
  const mine = take("/api/v1/skills?scope=mine");
  const usable = take("/api/v1/skills?scope=usable");
  providerCall.resolveWith({ json: provider });
  mine.resolveWith({ json: { items: lists.mine } });
  usable.resolveWith({ json: { items: lists.usable } });
  await pending;
  flushHeldChatPersist();
}

function count(url, method = "GET") {
  return calls.filter((call) => call.url === url && (call.options.method || "GET") === method).length;
}

async function settled() {
  for (let step = 0; step < 8; step += 1) {
    await Promise.resolve();
  }
}

beforeEach(() => {
  installFetch();
  holdChatPersistForTests(true);
  allowQa();
});

afterEach(() => {
  for (const app of apps) {
    app.unmount();
  }
  apps.length = 0;
  document.body.textContent = "";
  for (const call of calls) {
    if (!call.settled) {
      call.rejectWith(abortError());
    }
  }
  for (const session of sessions) {
    session.dispose();
  }
  sessions.length = 0;
  resetAccessForTests();
  resetChatRuntimeForTests();
  sessionStorage.clear();
  localStorage.clear();
  assigned.length = 0;
  location.pathname = "/chat";
  location.reloads = 0;
  cookieJar.clear();
  accessReady.value = false;
  perms.value = null;
  account.value = null;
  writeApiToken("");
});

test("A01 cancel leaves thread, draft, sending, model, storage, and the request alone", async () => {
  const session = openSession();
  await boot(session);
  session.selectedModel.value = "model-b";
  session.onModelChange();
  session.prompt.value = "还没发送";
  session.requestNewChat();
  assert.equal(session.confirmOpen.value, true);
  const before = sessionStorage.getItem(CHAT_THREAD_KEY);
  const model = sessionStorage.getItem(CHAT_MODEL_KEY);
  const epoch = currentAuthEpoch();
  session.cancelNewChat();
  assert.equal(session.confirmOpen.value, false);
  assert.equal(session.prompt.value, "还没发送");
  assert.equal(session.messages.value.length, 0);
  assert.equal(session.selectedModel.value, "model-b");
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY), before);
  assert.equal(sessionStorage.getItem(CHAT_MODEL_KEY), model);
  assert.equal(currentAuthEpoch(), epoch);
  assert.equal(count("/api/v1/llm/chat", "POST"), 0);

  session.prompt.value = "";
  session.messages.value = [{ role: "user", content: "已在页面上" }];
  session.requestNewChat();
  session.cancelNewChat();
  assert.equal(session.messages.value[0].content, "已在页面上");
  flushHeldChatPersist();
  assert.equal(readChatThread(sessionStorage)[0].content, "已在页面上");

  session.messages.value = [];
  session.prompt.value = "发送中的正文";
  const sending = session.send();
  const chat = take("/api/v1/llm/chat", "POST");
  assert.equal(chat.options.signal.aborted, false);
  session.requestNewChat();
  session.cancelNewChat();
  assert.equal(session.sending.value, true);
  assert.equal(chat.options.signal.aborted, false);
  assert.equal(session.messages.value.at(-1).content, "发送中的正文");
  chat.resolveWith({ json: { content: "答", model: "model-b" } });
  await sending;
  assert.equal(session.messages.value.at(-1).content, "答");
  flushHeldChatPersist();
  assert.equal(readChatThread(sessionStorage).at(-1).content, "答");
  assert.equal(location.pathname, "/chat");
  assert.equal(readAccount().username, "ada");
});

test("A02 confirm clears the tab, empty page is a no-op, and only an error can be cleared", async () => {
  const session = openSession();
  await boot(session);
  session.selectedModel.value = "model-b";
  session.onModelChange();
  session.messages.value = [
    {
      role: "assistant",
      content: "旧回答",
      actions: [{ type: "skill_drafted", id: SKILL_ID, name: "流程" }],
    },
  ];
  session.prompt.value = "草稿";
  session.error.value = "无法连接 API";
  const providerCalls = count("/api/v1/llm/provider");
  session.requestNewChat();
  assert.equal(session.confirmOpen.value, true);
  session.confirmNewChat();
  flushHeldChatPersist();
  assert.equal(session.confirmOpen.value, false);
  assert.equal(session.messages.value.length, 0);
  assert.equal(session.prompt.value, "");
  assert.equal(session.error.value, "");
  assert.equal(session.sending.value, false);
  assert.equal(session.selectedModel.value, "model-b");
  assert.equal(session.modelNotice.value, "");
  assert.deepEqual(readChatThread(sessionStorage), []);
  assert.equal(sessionStorage.getItem(CHAT_MODEL_KEY), "model-b");
  assert.equal(count("/api/v1/llm/provider"), providerCalls);
  assert.equal(count("/api/v1/llm/chat", "POST"), 0);
  assert.equal(calls.some((call) => call.options.method === "DELETE"), false);
  assert.equal(currentAuthEpoch(), 0);
  assert.equal(readAccount().username, "ada");

  const callsAfterClear = calls.length;
  const generation = (await import("./identity.js")).currentChatGeneration();
  session.requestNewChat();
  assert.equal(session.confirmOpen.value, false);
  assert.equal(calls.length, callsAfterClear);
  assert.equal(session.selectedModel.value, "model-b");
  assert.equal((await import("./identity.js")).currentChatGeneration(), generation);

  session.error.value = "发送失败";
  session.pageError.value = "无法读取模型状态";
  session.mode.value = "error";
  session.modelNotice.value = STALE_MODEL_TIP;
  session.requestNewChat();
  assert.equal(session.confirmOpen.value, false);
  assert.equal(session.error.value, "");
  assert.equal(session.pageError.value, "");
  assert.equal(session.modelNotice.value, "");
  assert.equal(session.selectedModel.value, "model-b");
  assert.equal(session.mode.value, "ready");
  assert.equal(calls.length, callsAfterClear);
});

test("A02 keeps an allowed model, falls back when it is not allowed, and off can still reset", async () => {
  const session = openSession();
  writeApiToken("machine-token-should-stay");
  sessionStorage.setItem(CHAT_MODEL_KEY, "retired-model");
  await boot(session);
  assert.equal(session.selectedModel.value, "model-a");
  assert.equal(session.modelNotice.value, STALE_MODEL_TIP);
  assert.equal(session.sendDisabled.value, false);

  session.selectedModel.value = "model-b";
  session.onModelChange();
  session.prompt.value = "换一页";
  const providers = count("/api/v1/llm/provider");
  session.requestNewChat();
  session.confirmNewChat();
  flushHeldChatPersist();
  assert.equal(session.selectedModel.value, "model-b");
  assert.equal(session.modelNotice.value, "");
  assert.equal(count("/api/v1/llm/provider"), providers);
  assert.equal(readApiToken(), "machine-token-should-stay");

  session.selectedModel.value = "retired-again";
  session.prompt.value = "不在列表";
  session.requestNewChat();
  session.confirmNewChat();
  assert.equal(session.selectedModel.value, "model-a");
  assert.equal(session.modelNotice.value, STALE_MODEL_TIP);
  assert.equal(sessionStorage.getItem(CHAT_MODEL_KEY), "model-a");

  const off = openSession();
  await boot(off, { provider: "off", models: ["model-a"], default_model: "model-a" });
  off.messages.value = [{ role: "user", content: "关闭模式" }];
  off.prompt.value = "草稿";
  const offProviders = count("/api/v1/llm/provider");
  off.requestNewChat();
  off.confirmNewChat();
  flushHeldChatPersist();
  assert.equal(off.messages.value.length, 0);
  assert.equal(off.prompt.value, "");
  assert.equal(off.provider.value.provider, "off");
  assert.equal(off.selectedModel.value, "model-a");
  assert.equal(count("/api/v1/llm/provider"), offProviders);
});

test("A02 an unknown catalog keeps the candidate and blocks send until a trusted set arrives", async () => {
  sessionStorage.setItem(CHAT_MODEL_KEY, "candidate-x");
  const session = openSession();
  const pending = session.load();
  const providerCall = take("/api/v1/llm/provider");
  const firstMine = take("/api/v1/skills?scope=mine");
  const firstUsable = take("/api/v1/skills?scope=usable");
  assert.equal(session.selectedModel.value, "candidate-x");
  assert.equal(session.modelSendBlocked.value, true);
  session.prompt.value = "先别发";
  await session.send();
  assert.equal(count("/api/v1/llm/chat", "POST"), 0);
  session.requestNewChat();
  session.confirmNewChat();
  assert.equal(session.selectedModel.value, "candidate-x");
  const again = take("/api/v1/llm/provider");
  const nextMine = take("/api/v1/skills?scope=mine");
  const nextUsable = take("/api/v1/skills?scope=usable");
  providerCall.resolveWith({ json: { provider: "openai", models: ["model-z"], default_model: "model-z" } });
  again.resolveWith({ json: OPENAI });
  firstMine.resolveWith({ json: { items: [] } });
  firstUsable.resolveWith({ json: { items: [] } });
  nextMine.resolveWith({ json: { items: [] } });
  nextUsable.resolveWith({ json: { items: [] } });
  await pending;
  await settled();
  flushHeldChatPersist();
  assert.equal(session.selectedModel.value, "model-a");
  assert.equal(session.modelNotice.value, STALE_MODEL_TIP);
  assert.equal(session.sendDisabled.value, false);
  assert.equal(sessionStorage.getItem(CHAT_MODEL_KEY), "model-a");
});

test("A03 late chat success, error, and finally stay out after reset and after the next send", async () => {
  const session = openSession();
  await boot(session);
  const logs = [];
  const originals = {};
  for (const name of ["log", "info", "debug", "warn", "error"]) {
    originals[name] = console[name];
    console[name] = (...args) => {
      logs.push(args.map((item) => String(item)).join(" "));
    };
  }
  try {
    session.prompt.value = "第一问";
    const first = session.send();
    const firstCall = take("/api/v1/llm/chat", "POST");
    firstCall.honorAbort = false;
    assert.equal(JSON.parse(firstCall.options.body).messages.at(-1).content, "第一问");
    assert.equal(firstCall.options.body.includes("system"), false);
    session.requestNewChat();
    session.confirmNewChat();
    flushHeldChatPersist();
    session.prompt.value = "第二问";
    const second = session.send();
    const secondCall = take("/api/v1/llm/chat", "POST");
    assert.equal(session.sending.value, true);
    firstCall.resolveWith({
      json: {
        content: "旧回答",
        model: "model-a",
        actions: [{ type: "skill_drafted", id: SKILL_ID, name: "流程" }],
        echo: "第一问",
      },
    });
    await first;
    flushHeldChatPersist();
    assert.equal(session.sending.value, true);
    assert.deepEqual(
      session.messages.value.map((item) => item.content),
      ["第二问"],
    );
    assert.equal(session.error.value, "");
    assert.equal(readChatThread(sessionStorage).some((item) => item.content === "旧回答"), false);
    assert.equal(logs.some((line) => line.includes("第一问")), false);

    session.dispose();
    sessions.pop();
    const errorSession = openSession();
    await boot(errorSession);
    errorSession.prompt.value = "会失败";
    const failing = errorSession.send();
    const failingCall = take("/api/v1/llm/chat", "POST");
    failingCall.honorAbort = false;
    errorSession.requestNewChat();
    errorSession.confirmNewChat();
    errorSession.prompt.value = "新的发送";
    const next = errorSession.send();
    const nextCall = take("/api/v1/llm/chat", "POST");
    failingCall.resolveWith({ status: 500, json: { detail: "旧错误", prompt: "会失败" } });
    await failing;
    flushHeldChatPersist();
    assert.equal(errorSession.sending.value, true);
    assert.equal(errorSession.error.value, "");
    assert.equal(errorSession.messages.value[0].content, "新的发送");
    assert.equal(errorSession.messages.value[0].failed, false);
    nextCall.resolveWith({ json: { content: "新回答" } });
    await next;
    assert.equal(errorSession.sending.value, false);
    assert.equal(errorSession.messages.value.at(-1).content, "新回答");
  } finally {
    for (const name of Object.keys(originals)) {
      console[name] = originals[name];
    }
  }
});

test("A03 AbortError is not reported as a network failure", async () => {
  const controller = new AbortController();
  const pending = requestJson("/api/v1/builds", { signal: controller.signal });
  const call = take("/api/v1/builds");
  controller.abort();
  await assert.rejects(pending, (caught) => {
    assert.equal(caught.name, "AbortError");
    assert.notEqual(caught.message, "无法连接 API");
    return true;
  });
  assert.equal(call.settled, true);

  globalThis.fetch = () => Promise.reject(new TypeError("socket hang up"));
  await assert.rejects(getJson("/api/v1/artifacts"), (caught) => {
    assert.equal(caught.message, "无法连接 API");
    assert.equal(caught.cause instanceof TypeError, true);
    return true;
  });
  installFetch();
  const ok = getJson("/api/v1/artifacts");
  const listed = take("/api/v1/artifacts");
  assert.equal(listed.options.signal, undefined);
  listed.resolveWith({ json: { items: ["kept"] } });
  assert.deepEqual(await ok, { items: ["kept"] });
});

test("A04 out-of-order model success, error, finally, and a newer selection stay put", async () => {
  const session = openSession();
  const firstLoad = session.load();
  const first = take("/api/v1/llm/provider");
  take("/api/v1/skills?scope=mine").resolveWith({ json: { items: [] } });
  take("/api/v1/skills?scope=usable").resolveWith({ json: { items: [] } });
  const secondLoad = session.load();
  const second = take("/api/v1/llm/provider");
  take("/api/v1/skills?scope=mine").resolveWith({ json: { items: [] } });
  take("/api/v1/skills?scope=usable").resolveWith({ json: { items: [] } });
  second.resolveWith({
    json: { provider: "openai", models: ["model-b"], default_model: "model-b" },
  });
  await secondLoad;
  first.resolveWith({ json: OPENAI });
  await firstLoad;
  flushHeldChatPersist();
  assert.equal(session.selectedModel.value, "model-b");
  assert.equal(session.mode.value, "ready");
  assert.equal(session.pageError.value, "");

  const thirdLoad = session.load();
  const third = take("/api/v1/llm/provider");
  take("/api/v1/skills?scope=mine").resolveWith({ json: { items: [] } });
  take("/api/v1/skills?scope=usable").resolveWith({ json: { items: [] } });
  session.selectedModel.value = "model-b";
  session.onModelChange();
  const fourthLoad = session.load();
  const fourth = take("/api/v1/llm/provider");
  take("/api/v1/skills?scope=mine").resolveWith({ json: { items: [] } });
  take("/api/v1/skills?scope=usable").resolveWith({ json: { items: [] } });
  fourth.resolveWith({ json: OPENAI });
  await fourthLoad;
  third.resolveWith({ status: 503, json: { detail: "旧的模型状态失败" } });
  await thirdLoad;
  flushHeldChatPersist();
  assert.equal(session.mode.value, "ready");
  assert.equal(session.pageError.value, "");
  assert.equal(session.selectedModel.value, "model-b");
  assert.equal(session.modelNotice.value, "");
  assert.equal(sessionStorage.getItem(CHAT_MODEL_KEY), "model-b");
});

test("A05 unmount, remount, and reload turn a pending send into an interrupted message", async () => {
  const session = openSession();
  await boot(session);
  session.prompt.value = "还没回来";
  const sending = session.send();
  const chat = take("/api/v1/llm/chat", "POST");
  chat.honorAbort = false;
  flushHeldChatPersist();
  assert.equal(session.messages.value.at(-1).pending, true);
  assert.equal(readChatThread(sessionStorage).at(-1).interrupted, true);
  session.dispose();
  sessions.pop();
  chat.resolveWith({ json: { content: "不该出现", actions: [{ type: "build_started", id: SKILL_ID, name: "包" }] } });
  await sending;
  const reloaded = openSession();
  assert.equal(reloaded.sending.value, false);
  assert.equal(reloaded.messages.value[0].content, "还没回来");
  assert.equal(reloaded.messages.value[0].interrupted, true);
  assert.equal(reloaded.messages.value[0].pending, undefined);
  assert.equal(reloaded.interruptNotice.value, INTERRUPTED_HINT);
  assert.equal(JSON.stringify(readChatThread(sessionStorage)).includes("pending"), false);
  await boot(reloaded);
  assert.equal(count("/api/v1/llm/chat", "POST"), 1);
  reloaded.prompt.value = "手工继续";
  const next = reloaded.send();
  const nextCall = take("/api/v1/llm/chat", "POST");
  const body = JSON.parse(nextCall.options.body);
  assert.equal(body.messages.some((item) => item.content === "还没回来"), false);
  assert.deepEqual(body.messages, [{ role: "user", content: "手工继续" }]);
  nextCall.resolveWith({ json: { content: "继续的回答" } });
  await next;
  flushHeldChatPersist();
  assert.equal(reloaded.sending.value, false);
  assert.equal(readChatThread(sessionStorage).some((item) => item.content === "不该出现"), false);
});

test("A05 a stored pending message is not sending and is not polled or retried", async () => {
  sessionStorage.setItem(
    CHAT_THREAD_KEY,
    JSON.stringify([
      {
        role: "user",
        content: "刷新前发出",
        pending: true,
        token: "secret-token",
        controller: { abort: true },
        raw: { choices: ["whole response"] },
        systemPrompt: "hidden",
      },
      { role: "system", content: "系统提示不要落盘" },
    ]),
  );
  const session = openSession();
  assert.equal(session.sending.value, false);
  assert.equal(session.messages.value.length, 1);
  assert.equal(session.messages.value[0].interrupted, true);
  assert.equal(session.interruptNotice.value, INTERRUPTED_HINT);
  const saved = sessionStorage.getItem(CHAT_THREAD_KEY);
  assert.equal(saved.includes("pending"), false);
  assert.equal(saved.includes("secret-token"), false);
  assert.equal(saved.includes("whole response"), false);
  assert.equal(saved.includes("系统提示不要落盘"), false);
  assert.equal(saved.includes("hidden"), false);
  await boot(session);
  assert.equal(count("/api/v1/llm/chat", "POST"), 0);
  assert.equal(session.sending.value, false);
});

test("A06 logout, current 401, another user, and a late 401 do not cross identities", async () => {
  localStorage.setItem(CHAT_THREAD_KEY, JSON.stringify([{ role: "user", content: "本地也清" }]));
  localStorage.setItem("kiln.account", "keep-me-out-of-chat");
  const session = openSession();
  await boot(session);
  session.prompt.value = "登出前";
  const sending = session.send();
  const chat = take("/api/v1/llm/chat", "POST");
  chat.honorAbort = false;
  const loggingOut = logoutSession(requestJson);
  const logoutCall = take("/api/v1/auth/logout", "POST");
  chat.resolveWith({ json: { content: "登出后的旧回答" } });
  logoutCall.resolveWith({ json: { ok: true } });
  await loggingOut;
  await sending;
  flushHeldChatPersist();
  assert.equal(session.messages.value.length, 0);
  assert.equal(session.prompt.value, "");
  assert.equal(session.selectedModel.value, "");
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY), null);
  assert.equal(sessionStorage.getItem(CHAT_MODEL_KEY), null);
  assert.equal(localStorage.getItem(CHAT_THREAD_KEY), null);
  assert.equal(readAccount(), null);
  assert.equal(readApiToken(), "");
  assert.deepEqual(assigned, ["/login"]);

  location.pathname = "/chat";
  assigned.length = 0;
  writeAccount({ id: "user-1", username: "ada", role_ids: ["qa"] });
  sessionStorage.setItem(CHAT_THREAD_KEY, JSON.stringify([{ role: "user", content: "过期前" }]));
  const expired = getJson("/api/v1/builds");
  const builds = take("/api/v1/builds");
  builds.resolveWith({ status: 401, json: { detail: "未登录" } });
  await assert.rejects(expired, (caught) => caught.status === 401 && caught.staleAuth !== true);
  flushHeldChatPersist();
  assert.equal(readAccount(), null);
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY), null);
  assert.equal(sessionStorage.getItem("kiln.flash"), "登录已过期，请重新登录");
  assert.deepEqual(assigned, ["/login"]);

  location.pathname = "/chat";
  assigned.length = 0;
  const staleBuilds = getJson("/api/v1/builds");
  const staleCall = take("/api/v1/builds");
  acceptLogin({ id: "user-2", username: "bea", role_ids: ["reader"] });
  sessionStorage.setItem(CHAT_THREAD_KEY, JSON.stringify([{ role: "user", content: "新用户自己的" }]));
  staleCall.resolveWith({ status: 401, json: { detail: "旧账号未登录" } });
  await assert.rejects(staleBuilds, (caught) => caught.staleAuth === true && caught.status === 401);
  flushHeldChatPersist();
  assert.equal(readAccount().username, "bea");
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY), JSON.stringify([{ role: "user", content: "新用户自己的" }]));
  assert.equal(assigned.length, 0);
  assert.equal(location.pathname, "/chat");
});

test("A06 same-user login rotation invalidates old chat and old generic 401s", async () => {
  const session = openSession();
  await boot(session);
  session.messages.value = [{ role: "user", content: "轮换前" }];
  session.prompt.value = "草稿";
  const rotating = getJson("/api/v1/executor");
  const executor = take("/api/v1/executor");
  const epoch = currentAuthEpoch();
  acceptLogin({ id: "user-1", username: "ada", role_ids: ["qa"] });
  assert.notEqual(currentAuthEpoch(), epoch);
  executor.resolveWith({ status: 401, json: { detail: "旧令牌" } });
  await assert.rejects(rotating, (caught) => caught.staleAuth === true);
  flushHeldChatPersist();
  assert.equal(readAccount().id, "user-1");
  assert.equal(readAccount().username, "ada");
  assert.equal(session.messages.value.length, 0);
  assert.equal(session.prompt.value, "");
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY), null);
  assert.equal(assigned.length, 0);

  const login = postJson("/api/v1/auth/login", { username: "ada", password: "secret-pass" });
  const loginCall = take("/api/v1/auth/login", "POST");
  assert.equal(loginCall.options.body.includes("secret-pass"), true);
  loginCall.resolveWith({ status: 401, json: { detail: "口令不正确" } });
  await assert.rejects(login, (caught) => caught.staleAuth !== true && caught.status === 401);
  assert.equal(readAccount().username, "ada");
  assert.equal(assigned.length, 0);
});

test("A07 sidebar refreshes are guarded and a reset does not undo a skill or build", async () => {
  const session = openSession();
  await boot(session, OPENAI, {
    mine: [{ id: SKILL_ID, name: "已有", version: "1", status: "draft" }],
  });
  assert.equal(session.mineItems.value[0].name, "已有");
  const reload = session.loadMine();
  const stale = take("/api/v1/skills?scope=mine");
  stale.honorAbort = false;
  session.messages.value = [{ role: "user", content: "清掉这页" }];
  session.requestNewChat();
  session.confirmNewChat();
  const fresh = take("/api/v1/skills?scope=mine");
  take("/api/v1/skills?scope=usable").resolveWith({ json: { items: [] } });
  stale.resolveWith({ json: { items: [{ id: SKILL_ID, name: "过期列表", version: "1", status: "draft" }] } });
  await reload;
  assert.equal(session.mineItems.value[0].name, "已有");
  fresh.resolveWith({ json: { items: [{ id: SKILL_ID, name: "刷新后", version: "1", status: "draft" }] } });
  await settled();
  flushHeldChatPersist();
  assert.equal(session.mineItems.value.length, 1);
  assert.equal(session.mineItems.value[0].name, "刷新后");
  assert.equal(calls.some((call) => call.options.method === "DELETE"), false);
  assert.equal(NEW_CHAT_WARNING.includes("已创建的 Skill 和已发起的构建仍会保留"), true);
  assert.equal(NEW_CHAT_WARNING.includes("停止等待不能撤销服务器已执行或仍在执行的操作"), true);
  assert.equal(NEW_CHAT_WARNING.includes("已取消构建"), false);
});

test("A08 reader, mock, and permission gates stay where they were", async () => {
  assert.equal(ROUTE_GATES.chat, "agent.qa");
  assert.deepEqual(ACTION_GATES["chat.send"], ["agent.qa"]);
  assert.deepEqual(ACTION_GATES["agent.draft_skill"], ["agent.draft_skill"]);
  assert.deepEqual(ACTION_GATES["agent.build_artifact"], ["agent.build_artifact"]);
  assert.equal(Object.hasOwn(ACTION_GATES, "build.runtime.provision"), false);
  assert.equal(canSee("chat", new Set(["skill.read"])), false);
  assert.equal(canDo("chat.send", new Set(["executor.run", "skill.publish"])), false);

  perms.value = new Set(["skill.read"]);
  const reader = openSession();
  await reader.load();
  assert.equal(reader.forbidden.value, true);
  reader.requestNewChat();
  reader.prompt.value = "读者不能发";
  await reader.send();
  assert.equal(count("/api/v1/llm/provider"), 0);
  assert.equal(count("/api/v1/llm/chat", "POST"), 0);

  perms.value = QA;
  const mock = openSession();
  await boot(mock, { provider: "mock", models: ["model-a"], default_model: "model-a" });
  assert.equal(mock.mock.value, true);
  mock.prompt.value = "模拟一句";
  const sent = mock.send();
  const chat = take("/api/v1/llm/chat", "POST");
  assert.equal(Object.hasOwn(JSON.parse(chat.options.body), "model"), false);
  chat.resolveWith({ json: { content: "模拟回答" } });
  await sent;
  assert.equal(mock.messages.value.at(-1).content, "模拟回答");
  assert.equal(mock.messages.value.at(-1).actions.length, 0);
  mock.requestNewChat();
  mock.confirmNewChat();
  flushHeldChatPersist();
  assert.equal(mock.messages.value.length, 0);
  assert.equal(mock.provider.value.provider, "mock");
});

test("storage watchers acquire the current generation and do not restore a cleared thread", async () => {
  const storage = memoryStorage();
  storage.setItem(CHAT_THREAD_KEY, "[]");
  const view = openChatView();
  scheduleChatPersist(
    view.viewToken,
    () => view.isAlive(),
    () => [{ role: "user", content: "旧线程" }],
    storage,
  );
  advanceChatGeneration();
  storage.setItem(CHAT_THREAD_KEY, "[]");
  flushHeldChatPersist();
  assert.equal(storage.getItem(CHAT_THREAD_KEY), "[]");
  assert.equal(storage.getItem(CHAT_THREAD_KEY).includes("旧线程"), false);

  const session = openSession();
  await boot(session);
  session.messages.value = [{ role: "user", content: "会回来吗" }];
  session.prompt.value = "未发送草稿";
  session.requestNewChat();
  session.confirmNewChat();
  flushHeldChatPersist();
  assert.equal(session.messages.value.length, 0);
  assert.equal(session.prompt.value, "");
  assert.deepEqual(readChatThread(sessionStorage), []);
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY).includes("会回来吗"), false);
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY).includes("未发送草稿"), false);

  const other = memoryStorage();
  other.setItem(CHAT_THREAD_KEY, JSON.stringify([{ role: "user", content: "其它标签" }]));
  localStorage.setItem(CHAT_MODEL_KEY, "别的标签页");
  assert.equal(JSON.parse(other.getItem(CHAT_THREAD_KEY))[0].content, "其它标签");
  assert.equal(localStorage.getItem(CHAT_MODEL_KEY), "别的标签页");
  view.close();
});

test("an unheld watcher flush persists the live thread and still skips a stale job", async () => {
  holdChatPersistForTests(false);
  const session = openSession();
  await boot(session);
  session.messages.value = [{ role: "user", content: "自动落盘" }];
  await nextTick();
  await Promise.resolve();
  flushChatPersist();
  assert.equal(readChatThread(sessionStorage)[0].content, "自动落盘");
});

function compileVue(file) {
  if (compiledViews.has(file)) {
    return compiledViews.get(file);
  }
  const source = readVue(file);
  const parsed = parse(source, { filename: file });
  if (parsed.errors.length) {
    throw parsed.errors[0];
  }
  const script = compileScript(parsed.descriptor, { id: file, inlineTemplate: true });
  const code = script.content.replace(/from\s+["']([^"']+)["']/g, (full, spec) => {
    if (!spec.startsWith(".")) {
      return full;
    }
    if (spec.endsWith(".vue")) {
      const child = compileVue(resolve(dirname(file), spec));
      return `from ${JSON.stringify(pathToFileURL(child).href)}`;
    }
    let target = resolve(dirname(file), spec);
    if (!target.endsWith(".js")) {
      target += ".js";
    }
    return `from ${JSON.stringify(pathToFileURL(target).href)}`;
  });
  const output = resolve(viewBuildDir, `${compiledViews.size}.mjs`);
  writeFileSync(output, code);
  compiledViews.set(file, output);
  return output;
}

function readVue(file) {
  return readFileSync(file, "utf8");
}

function buttonsNamed(text) {
  return document.body.querySelectorAll("button").filter((button) => button.textContent.trim() === text);
}

async function mountChatView() {
  const source = resolve(dirname(fileURLToPath(import.meta.url)), "views/ChatView.vue");
  const component = (await import(pathToFileURL(compileVue(source)).href)).default;
  const { createApp } = await import("vue");
  const { createMemoryHistory, createRouter } = await import("vue-router");
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "overview", component: { template: "<div />" } },
      { path: "/chat", name: "chat", component },
      { path: "/login", name: "login", component: { template: "<div />" } },
      { path: "/skills/:id", name: "skill", component: { template: "<div />" } },
      { path: "/builds", name: "builds", component: { template: "<div />" } },
      { path: "/skill-new", name: "skill-new", component: { template: "<div />" } },
    ],
  });
  await router.push("/chat");
  await router.isReady();
  const root = document.createElement("div");
  document.body.appendChild(root);
  const app = createApp(component);
  app.use(router);
  app.mount(root);
  apps.push(app);
  await nextTick();
  return app;
}

async function finishInitialLoad(provider = OPENAI) {
  const providerCall = take("/api/v1/llm/provider");
  const mine = take("/api/v1/skills?scope=mine");
  const usable = take("/api/v1/skills?scope=usable");
  providerCall.resolveWith({ json: provider });
  mine.resolveWith({ json: { items: [] } });
  usable.resolveWith({ json: { items: [] } });
  await settled();
  await nextTick();
}

test("the mounted page has one 新建对话 button and the exact confirmation", async () => {
  await mountChatView();
  await finishInitialLoad();
  assert.equal(buttonsNamed("新建对话").length, 1);
  assert.equal(document.body.textContent.includes(NEW_CHAT_WARNING), false);
  buttonsNamed("新建对话")[0].click();
  await nextTick();
  assert.equal(document.body.textContent.includes(NEW_CHAT_WARNING), false);
  assert.equal(document.body.querySelector('[role="alertdialog"]'), null);
  assert.equal(buttonsNamed("重试读取模型状态").length, 0);

  const textarea = document.body.querySelector("textarea");
  textarea.value = "未发送草稿";
  textarea.dispatchEvent({ type: "input", preventDefault() {}, stopPropagation() {} });
  await nextTick();
  buttonsNamed("新建对话")[0].click();
  await nextTick();
  const dialog = document.body.querySelector('[role="alertdialog"]');
  assert.ok(dialog);
  assert.equal(dialog.textContent.includes(NEW_CHAT_WARNING), true);
  assert.equal(buttonsNamed("新建对话").length, 1);
  buttonsNamed("取消")[0].click();
  await nextTick();
  flushHeldChatPersist();
  assert.equal(document.body.querySelector('[role="alertdialog"]'), null);
  assert.equal(textarea.value, "未发送草稿");
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY) || "[]", "[]");

  buttonsNamed("新建对话")[0].click();
  await nextTick();
  buttonsNamed("确认")[0].click();
  await nextTick();
  flushHeldChatPersist();
  assert.equal(textarea.value, "");
  assert.equal(document.body.textContent.includes("未发送草稿"), false);
  assert.equal(document.body.textContent.includes(NEW_CHAT_WARNING), false);
  assert.deepEqual(readChatThread(sessionStorage), []);
});

test("a cold provider failure stays recoverable after 新建对话 clears the error", async () => {
  const epoch = currentAuthEpoch();
  await mountChatView();
  const failed = take("/api/v1/llm/provider");
  take("/api/v1/skills?scope=mine").resolveWith({ json: { items: [] } });
  take("/api/v1/skills?scope=usable").resolveWith({ json: { items: [] } });
  failed.resolveWith({ status: 503, json: { detail: "模型状态暂不可用" } });
  await settled();
  await nextTick();
  assert.equal(document.body.textContent.includes("模型状态暂不可用"), true);
  assert.equal(buttonsNamed("重试").length, 1);
  const beforeReset = count("/api/v1/llm/provider");
  buttonsNamed("新建对话")[0].click();
  await nextTick();
  assert.equal(document.body.querySelector('[role="alertdialog"]'), null);
  assert.equal(document.body.textContent.includes("模型状态暂不可用"), false);
  assert.equal(count("/api/v1/llm/provider"), beforeReset);
  assert.equal(count("/api/v1/llm/chat", "POST"), 0);
  const retry = buttonsNamed("重试读取模型状态");
  assert.equal(retry.length, 1);
  assert.notEqual(document.body.querySelector("textarea").getAttribute("disabled"), "false");
  retry[0].click();
  await settled();
  const recovered = take("/api/v1/llm/provider");
  take("/api/v1/skills?scope=mine").resolveWith({ json: { items: [] } });
  take("/api/v1/skills?scope=usable").resolveWith({ json: { items: [] } });
  recovered.resolveWith({ json: OPENAI });
  await settled();
  await nextTick();
  const textarea = document.body.querySelector("textarea");
  assert.equal(textarea.getAttribute("disabled"), "false");
  assert.equal(buttonsNamed("重试读取模型状态").length, 0);
  textarea.value = "恢复后才写";
  textarea.dispatchEvent({ type: "input", preventDefault() {}, stopPropagation() {} });
  await nextTick();
  const send = [...document.body.querySelectorAll("button")].find((button) => button.getAttribute("type") === "submit");
  assert.ok(send);
  assert.equal(send.getAttribute("disabled"), "false");
  assert.equal(count("/api/v1/llm/chat", "POST"), 0);
  assert.equal(currentAuthEpoch(), epoch);
  assert.equal(assigned.length, 0);
  assert.equal(readAccount().username, "ada");
});

test("a cached but untrusted provider keeps status recovery after the error is cleared", async () => {
  const epoch = currentAuthEpoch();
  await mountChatView();
  await finishInitialLoad();
  const textarea = document.body.querySelector("textarea");
  textarea.value = "这个模型不被允许";
  textarea.dispatchEvent({ type: "input", preventDefault() {}, stopPropagation() {} });
  await nextTick();
  document.body.querySelector("form").dispatchEvent({
    type: "submit",
    preventDefault() {},
    stopPropagation() {},
  });
  await settled();
  const chat = take("/api/v1/llm/chat", "POST");
  chat.resolveWith({ status: 400, json: { detail: MODEL_NOT_ALLOWED } });
  await settled();
  const revalidate = take("/api/v1/llm/provider");
  revalidate.resolveWith({ status: 503, json: { detail: "重新校验失败" } });
  await settled();
  await nextTick();
  buttonsNamed("新建对话")[0].click();
  await nextTick();
  assert.ok(document.body.querySelector('[role="alertdialog"]'));
  buttonsNamed("确认")[0].click();
  await settled();
  const afterConfirm = take("/api/v1/llm/provider");
  take("/api/v1/skills?scope=mine").resolveWith({ json: { items: [] } });
  take("/api/v1/skills?scope=usable").resolveWith({ json: { items: [] } });
  afterConfirm.resolveWith({ status: 503, json: { detail: "确认后的状态失败" } });
  await settled();
  await nextTick();
  assert.equal(document.body.textContent.includes("确认后的状态失败"), true);
  const posts = count("/api/v1/llm/chat", "POST");
  const providers = count("/api/v1/llm/provider");
  buttonsNamed("新建对话")[0].click();
  await nextTick();
  assert.equal(document.body.querySelector('[role="alertdialog"]'), null);
  assert.equal(document.body.textContent.includes("确认后的状态失败"), false);
  assert.equal(count("/api/v1/llm/provider"), providers);
  assert.equal(count("/api/v1/llm/chat", "POST"), posts);
  const retry = buttonsNamed("重试读取模型状态");
  assert.equal(retry.length, 1);
  assert.notEqual(document.body.querySelector("textarea").getAttribute("disabled"), "false");
  retry[0].click();
  await settled();
  const recovered = take("/api/v1/llm/provider");
  take("/api/v1/skills?scope=mine").resolveWith({ json: { items: [] } });
  take("/api/v1/skills?scope=usable").resolveWith({ json: { items: [] } });
  recovered.resolveWith({ json: OPENAI });
  await settled();
  await nextTick();
  assert.equal(buttonsNamed("重试读取模型状态").length, 0);
  const composer = document.body.querySelector("textarea");
  assert.equal(composer.getAttribute("disabled"), "false");
  composer.value = "信任恢复后";
  composer.dispatchEvent({ type: "input", preventDefault() {}, stopPropagation() {} });
  await nextTick();
  const send = [...document.body.querySelectorAll("button")].find((button) => button.getAttribute("type") === "submit");
  assert.ok(send);
  assert.equal(send.getAttribute("disabled"), "false");
  assert.equal(count("/api/v1/llm/chat", "POST"), posts);
  assert.equal(currentAuthEpoch(), epoch);
  assert.equal(assigned.length, 0);
});

test("the mounted page drops a late reply after confirm and hides the chat from a reader", async () => {
  await mountChatView();
  await finishInitialLoad();
  const textarea = document.body.querySelector("textarea");
  textarea.value = "第一问";
  textarea.dispatchEvent({ type: "input", preventDefault() {}, stopPropagation() {} });
  await nextTick();
  document.body.querySelector("form").dispatchEvent({
    type: "submit",
    preventDefault() {},
    stopPropagation() {},
  });
  await settled();
  const chat = take("/api/v1/llm/chat", "POST");
  chat.honorAbort = false;
  assert.equal(document.body.textContent.includes("第一问"), true);
  buttonsNamed("新建对话")[0].click();
  await nextTick();
  buttonsNamed("确认")[0].click();
  await nextTick();
  chat.resolveWith({
    json: {
      content: "旧回答",
      actions: [{ type: "build_started", id: SKILL_ID, name: "包" }],
    },
  });
  await settled();
  await nextTick();
  flushHeldChatPersist();
  assert.equal(document.body.textContent.includes("旧回答"), false);
  assert.equal(document.body.textContent.includes("第一问"), false);
  assert.equal(document.body.textContent.includes("已取消构建"), false);
  assert.deepEqual(readChatThread(sessionStorage), []);

  apps.at(-1).unmount();
  apps.pop();
  document.body.textContent = "";
  perms.value = new Set(["skill.read"]);
  await mountChatView();
  await nextTick();
  assert.equal(buttonsNamed("新建对话").length, 0);
  assert.equal(document.body.querySelector("textarea"), null);
  assert.equal(document.body.textContent.includes("你没有查看此页面的权限"), true);
  assert.equal(count("/api/v1/llm/chat", "POST"), 1);
});

test("mounted login keeps a new account when an older generic 401 arrives", async () => {
  sessionStorage.setItem(CHAT_THREAD_KEY, JSON.stringify([{ role: "user", content: "旧用户" }]));
  sessionStorage.setItem(CHAT_MODEL_KEY, "model-a");
  const stale = getJson("/api/v1/builds");
  const staleCall = take("/api/v1/builds");
  const source = resolve(dirname(fileURLToPath(import.meta.url)), "views/LoginView.vue");
  const component = (await import(pathToFileURL(compileVue(source)).href)).default;
  const { createApp } = await import("vue");
  const { createMemoryHistory, createRouter } = await import("vue-router");
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "overview", component: { template: "<div>概览</div>" } },
      { path: "/login", name: "login", component },
    ],
  });
  await router.push("/login");
  await router.isReady();
  const root = document.createElement("div");
  document.body.appendChild(root);
  const app = createApp(component);
  app.use(router);
  app.mount(root);
  apps.push(app);
  await nextTick();
  take("/api/v1/auth/sync").resolveWith({ json: { configured: false } });
  await settled();
  const username = document.body.querySelector("#username");
  const password = document.body.querySelector("#password");
  username.value = "bea";
  username.dispatchEvent({ type: "input", preventDefault() {}, stopPropagation() {} });
  password.value = "secret-pass";
  password.dispatchEvent({ type: "input", preventDefault() {}, stopPropagation() {} });
  await nextTick();
  document.body.querySelector("form").dispatchEvent({
    type: "submit",
    preventDefault() {},
    stopPropagation() {},
  });
  await settled();
  const login = take("/api/v1/auth/login", "POST");
  assert.equal(login.options.body.includes("secret-pass"), true);
  login.resolveWith({
    json: { account: { id: "user-2", username: "bea", display_name: "Bea", role_ids: ["reader"] } },
  });
  await settled();
  staleCall.resolveWith({ status: 401, json: { detail: "旧账号未登录" } });
  await assert.rejects(stale, (caught) => caught.staleAuth === true && caught.status === 401);
  flushHeldChatPersist();
  assert.equal(readAccount().username, "bea");
  assert.equal(readAccount().id, "user-2");
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY), null);
  assert.equal(sessionStorage.getItem(CHAT_MODEL_KEY), null);
  assert.equal(assigned.length, 0);
  assert.equal(location.pathname, "/chat");
});

test("a late chat or status 401 after reset does not expire the current identity", async () => {
  const session = openSession();
  await boot(session);
  session.prompt.value = "仍在这一次";
  const live = session.send();
  const liveCall = take("/api/v1/llm/chat", "POST");
  liveCall.resolveWith({ status: 401, json: { detail: "未登录" } });
  await live;
  assert.equal(readAccount(), null);
  assert.deepEqual(assigned, ["/login"]);

  location.pathname = "/chat";
  assigned.length = 0;
  allowQa();
  const again = openSession();
  await boot(again);
  again.prompt.value = "会被清掉";
  const first = again.send();
  const firstCall = take("/api/v1/llm/chat", "POST");
  firstCall.honorAbort = false;
  again.requestNewChat();
  again.confirmNewChat();
  again.prompt.value = "下一问";
  const second = again.send();
  const secondCall = take("/api/v1/llm/chat", "POST");
  firstCall.resolveWith({ status: 401, json: { detail: "旧对话未登录" } });
  await first;
  flushHeldChatPersist();
  assert.equal(readAccount().username, "ada");
  assert.equal(assigned.length, 0);
  assert.equal(location.pathname, "/chat");
  assert.equal(again.sending.value, true);
  assert.deepEqual(again.messages.value.map((item) => item.content), ["下一问"]);
  secondCall.resolveWith({ json: { content: "下一答" } });
  await second;
  assert.equal(again.messages.value.at(-1).content, "下一答");

  const status = again.load();
  const statusCall = take("/api/v1/llm/provider");
  statusCall.honorAbort = false;
  take("/api/v1/skills?scope=mine").resolveWith({ json: { items: [] } });
  take("/api/v1/skills?scope=usable").resolveWith({ json: { items: [] } });
  again.messages.value = [{ role: "user", content: "再清一次" }];
  again.requestNewChat();
  again.confirmNewChat();
  statusCall.resolveWith({ status: 401, json: { detail: "旧状态未登录" } });
  await status;
  await settled();
  assert.equal(readAccount().username, "ada");
  assert.equal(assigned.length, 0);
  assert.equal(again.mode.value, "ready");
  assert.equal(again.pageError.value, "");

  const builds = getJson("/api/v1/builds");
  const buildsCall = take("/api/v1/builds");
  buildsCall.resolveWith({ status: 401, json: { detail: "当前请求未登录" } });
  await assert.rejects(builds, (caught) => caught.staleAuth !== true && caught.status === 401);
  assert.equal(readAccount(), null);
  assert.deepEqual(assigned, ["/login"]);

  location.pathname = "/chat";
  assigned.length = 0;
  allowQa();
  const controller = new AbortController();
  const canceled = getJson("/api/v1/artifacts", { signal: controller.signal });
  const canceledCall = take("/api/v1/artifacts");
  canceledCall.honorAbort = false;
  controller.abort();
  canceledCall.resolveWith({ status: 401, json: { detail: "已取消的请求" } });
  await assert.rejects(canceled, (caught) => caught.staleAuth === true && caught.status === 401);
  assert.equal(readAccount().username, "ada");
  assert.equal(assigned.length, 0);
});

test("a trusted status refresh can be reset without leaving the composer disabled", async () => {
  const session = openSession();
  await boot(session);
  session.selectedModel.value = "model-b";
  session.onModelChange();
  session.messages.value = [{ role: "user", content: "刷新时清空" }];
  const refresh = session.load();
  const providerCall = take("/api/v1/llm/provider");
  providerCall.honorAbort = false;
  take("/api/v1/skills?scope=mine").resolveWith({ json: { items: [] } });
  take("/api/v1/skills?scope=usable").resolveWith({ json: { items: [] } });
  assert.equal(session.mode.value, "loading");
  assert.equal(session.catalogTrusted.value, true);
  const providers = count("/api/v1/llm/provider");
  session.requestNewChat();
  session.confirmNewChat();
  flushHeldChatPersist();
  assert.equal(session.mode.value, "ready");
  assert.equal(session.pageError.value, "");
  assert.equal(session.selectedModel.value, "model-b");
  assert.equal(session.modelNotice.value, "");
  assert.equal(session.composerDisabled.value, false);
  assert.equal(session.modelSendBlocked.value, false);
  assert.equal(count("/api/v1/llm/provider"), providers);
  assert.equal(count("/api/v1/llm/chat", "POST"), 0);
  providerCall.resolveWith({
    status: 500,
    json: { detail: "旧的刷新失败" },
  });
  await refresh;
  await settled();
  assert.equal(session.mode.value, "ready");
  assert.equal(session.pageError.value, "");
  assert.equal(session.selectedModel.value, "model-b");
  assert.equal(readAccount().username, "ada");

  const next = openSession();
  await boot(next);
  next.messages.value = [{ role: "user", content: "成功也会晚到" }];
  const late = next.load();
  const lateCall = take("/api/v1/llm/provider");
  lateCall.honorAbort = false;
  take("/api/v1/skills?scope=mine").resolveWith({ json: { items: [] } });
  take("/api/v1/skills?scope=usable").resolveWith({ json: { items: [] } });
  next.requestNewChat();
  next.confirmNewChat();
  const afterReset = count("/api/v1/llm/provider");
  lateCall.resolveWith({
    json: { provider: "openai", models: ["model-z"], default_model: "model-z" },
  });
  await late;
  await settled();
  assert.equal(next.mode.value, "ready");
  assert.equal(next.selectedModel.value, "model-b");
  assert.equal(next.modelNotice.value, "");
  assert.equal(next.selectedModel.value === "model-z", false);
  assert.equal(count("/api/v1/llm/provider"), afterReset);
  assert.equal(count("/api/v1/llm/chat", "POST"), 0);
});

test("a model change made after the winning status request started is kept", async () => {
  const session = openSession();
  await boot(session);
  assert.equal(session.selectedModel.value, "model-a");
  const pending = session.load();
  const call = take("/api/v1/llm/provider");
  take("/api/v1/skills?scope=mine").resolveWith({ json: { items: [] } });
  take("/api/v1/skills?scope=usable").resolveWith({ json: { items: [] } });
  session.selectedModel.value = "model-b";
  session.onModelChange();
  call.resolveWith({ json: OPENAI });
  await pending;
  await settled();
  assert.equal(session.selectedModel.value, "model-b");
  assert.equal(session.modelNotice.value, "");
  assert.equal(sessionStorage.getItem(CHAT_MODEL_KEY), "model-b");
  assert.equal(session.mode.value, "ready");
});

test("replacing the API token clears the chat before reload for the same user and a new credential", async () => {
  writeApiToken("credential-old");
  sessionStorage.setItem(CHAT_MODEL_KEY, "model-b");
  const session = openSession();
  await boot(session);
  session.selectedModel.value = "model-b";
  session.onModelChange();
  session.prompt.value = "换令牌前";
  const sending = session.send();
  const chat = take("/api/v1/llm/chat", "POST");
  chat.honorAbort = false;
  let seenAtReload = null;
  location.reload = function reload() {
    this.reloads += 1;
    seenAtReload = {
      thread: sessionStorage.getItem(CHAT_THREAD_KEY),
      model: sessionStorage.getItem(CHAT_MODEL_KEY),
      token: readApiToken(),
      account: readAccount(),
    };
  };
  const obsolete = getJson("/api/v1/builds");
  const obsoleteCall = take("/api/v1/builds");
  replaceApiToken("credential-new");
  assert.equal(location.reloads, 1);
  assert.equal(seenAtReload.thread, null);
  assert.equal(seenAtReload.model, null);
  assert.equal(seenAtReload.token, "credential-new");
  assert.equal(seenAtReload.account, null);
  assert.equal(currentAuthEpoch() > 0, true);
  chat.resolveWith({ json: { content: "旧令牌的回答" } });
  await sending;
  flushHeldChatPersist();
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY), null);
  assert.equal(session.messages.value.length, 0);
  writeAccount({ id: "user-1", username: "ada", role_ids: ["qa"] });
  const remounted = openSession();
  assert.equal(remounted.messages.value.length, 0);
  assert.equal(remounted.selectedModel.value, "");
  obsoleteCall.resolveWith({ status: 401, json: { detail: "旧令牌未登录" } });
  await assert.rejects(obsolete, (caught) => caught.staleAuth === true && caught.status === 401);
  await settled();
  assert.equal(readApiToken(), "credential-new");
  assert.equal(readAccount().id, "user-1");
  assert.equal(location.reloads, 1);
  assert.equal(assigned.length, 0);
  assert.equal(remounted.messages.value.length, 0);

  replaceApiToken("credential-other");
  assert.equal(readApiToken(), "credential-other");
  assert.equal(readAccount(), null);
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY), null);
  assert.equal(sessionStorage.getItem(CHAT_MODEL_KEY), null);
  assert.equal(location.reloads, 2);
  flushHeldChatPersist();
  const other = openSession();
  assert.equal(other.messages.value.length, 0);
  assert.equal(other.selectedModel.value, "");
});

async function loadLoginComponent() {
  const source = resolve(dirname(fileURLToPath(import.meta.url)), "views/LoginView.vue");
  return (await import(pathToFileURL(compileVue(source)).href)).default;
}

async function mountLogin(router) {
  const component = await loadLoginComponent();
  const { createApp, h } = await import("vue");
  const { createMemoryHistory, createRouter, RouterView } = await import("vue-router");
  const activeRouter = router || createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "overview", component: { render: () => h("div", "概览") } },
      { path: "/login", name: "login", component },
    ],
  });
  await activeRouter.push("/login");
  await activeRouter.isReady();
  const pushes = [];
  const originalPush = activeRouter.push.bind(activeRouter);
  activeRouter.push = (to) => {
    pushes.push(typeof to === "string" ? to : to?.path);
    return originalPush(to);
  };
  const root = document.createElement("div");
  document.body.appendChild(root);
  const app = createApp({ render: () => h(RouterView) });
  app.use(activeRouter);
  app.mount(root);
  apps.push(app);
  await nextTick();
  return { app, router: activeRouter, component, pushes };
}

function submitLoginForm() {
  document.body.querySelector("form").dispatchEvent({
    type: "submit",
    preventDefault() {},
    stopPropagation() {},
  });
}

test("an older login cannot overwrite a newer login, and a 401 during login does not navigate", async () => {
  const firstMount = await mountLogin();
  take("/api/v1/auth/sync").resolveWith({ json: { configured: false } });
  await settled();
  submitLoginForm();
  await settled();
  const firstLogin = take("/api/v1/auth/login", "POST");
  const during = getJson("/api/v1/builds");
  const duringCall = take("/api/v1/builds");
  duringCall.resolveWith({ status: 401, json: { detail: "登录过程中的旧 401" } });
  await assert.rejects(during, (caught) => caught.status === 401 && caught.staleAuth !== true);
  assert.equal(assigned.length, 0);
  assert.equal(location.pathname, "/chat");
  assert.equal(document.body.textContent.includes("登录过程中的旧 401"), false);
  assert.equal(document.body.textContent.includes("正在登录"), true);
  firstLogin.resolveWith({
    json: { account: { id: "user-2", username: "bea", display_name: "Bea", role_ids: ["reader"] } },
  });
  for (let step = 0; step < 5; step += 1) {
    await nextTick();
  }
  assert.equal(readAccount().username, "bea");
  assert.deepEqual(firstMount.pushes, ["/"]);
  assert.equal(document.body.textContent.includes("登录失败"), false);
  assert.equal(assigned.length, 0);

  const { createMemoryHistory, createRouter } = await import("vue-router");
  const { h } = await import("vue");
  const Login = await loadLoginComponent();
  const shared = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "overview", component: { render: () => h("div", "概览") } },
      { path: "/login", name: "login", component: Login },
    ],
  });
  const older = await mountLogin(shared);
  const olderSync = take("/api/v1/auth/sync");
  await settled();
  submitLoginForm();
  await settled();
  const olderCall = take("/api/v1/auth/login", "POST");
  older.app.unmount();
  apps.pop();
  document.body.textContent = "";
  const newer = await mountLogin(shared);
  const newerSync = take("/api/v1/auth/sync");
  olderSync.resolveWith({ json: { configured: true } });
  await settled();
  assert.equal(document.body.textContent.includes("使用公司账号登录"), false);
  newerSync.resolveWith({ json: { configured: false } });
  await settled();
  submitLoginForm();
  await settled();
  olderCall.resolveWith({
    json: { account: { id: "user-1", username: "ada", display_name: "Ada", role_ids: ["qa"] } },
  });
  await settled();
  const newerCall = take("/api/v1/auth/login", "POST");
  newerCall.resolveWith({
    json: { account: { id: "user-9", username: "bea", display_name: "Bea", role_ids: ["reader"] } },
  });
  await settled();
  flushHeldChatPersist();
  assert.equal(readAccount().username, "bea");
  assert.equal(readAccount().id, "user-9");
  assert.deepEqual(newer.pushes, ["/"]);
  assert.equal(document.body.textContent.includes("登录失败"), false);

  const racing = await mountLogin();
  take("/api/v1/auth/sync").resolveWith({ json: { configured: false } });
  await settled();
  submitLoginForm();
  await settled();
  const earlyCall = take("/api/v1/auth/login", "POST");
  submitLoginForm();
  await settled();
  earlyCall.resolveWith({ status: 500, json: { detail: "旧登录失败" } });
  await settled();
  const laterCall = take("/api/v1/auth/login", "POST");
  assert.equal(document.body.textContent.includes("旧登录失败"), false);
  assert.equal(document.body.textContent.includes("正在登录"), true);
  laterCall.resolveWith({
    json: { account: { id: "user-3", username: "cy", display_name: "Cy", role_ids: ["reader"] } },
  });
  await settled();
  assert.equal(readAccount().username, "cy");
  assert.deepEqual(racing.pushes, ["/"]);
  assert.equal(document.body.textContent.includes("旧登录失败"), false);
});

test("logout success or failure after a newer login keeps the account and drops the old bearer", async () => {
  writeApiToken("session-token");
  const loggingOut = logoutSession(requestJson);
  const logoutCall = take("/api/v1/auth/logout", "POST");
  acceptLogin({ id: "user-2", username: "bea", role_ids: ["reader"] });
  logoutCall.resolveWith({ json: { ok: true } });
  await loggingOut;
  flushHeldChatPersist();
  assert.equal(readAccount().username, "bea");
  assert.equal(readApiToken(), "");
  assert.equal(sessionStorage.getItem("kiln.apiToken"), null);
  assert.equal(assigned.length, 0);

  writeApiToken("session-token");
  const failing = logoutSession(requestJson);
  const failingCall = take("/api/v1/auth/logout", "POST");
  acceptLogin({ id: "user-2", username: "bea", role_ids: ["reader"] });
  failingCall.rejectWith(new TypeError("logout dropped"));
  await failing;
  flushHeldChatPersist();
  assert.equal(readAccount().username, "bea");
  assert.equal(readApiToken(), "");
  assert.equal(assigned.length, 0);
});

test("a current 401 during logout still removes the api token when nothing replaces the session", async () => {
  writeApiToken("logout-token");
  writeAccount({ id: "user-1", username: "ada", role_ids: ["qa"] });
  sessionStorage.setItem(CHAT_THREAD_KEY, JSON.stringify([{ role: "user", content: "登出中" }]));
  sessionStorage.setItem(CHAT_MODEL_KEY, "model-b");
  location.pathname = "/chat";
  assigned.length = 0;
  const loggingOut = logoutSession(requestJson);
  const logoutCall = take("/api/v1/auth/logout", "POST");
  const expired = getJson("/api/v1/builds");
  const expiredCall = take("/api/v1/builds");
  assert.equal(logoutCall.options.signal.aborted, false);
  expiredCall.resolveWith({ status: 401, json: { detail: "登出过程中的 401" } });
  await assert.rejects(expired, (caught) => caught.status === 401 && caught.staleAuth !== true);
  assert.equal(logoutCall.options.signal.aborted, true);
  await loggingOut;
  assert.equal(readApiToken(), "");
  assert.equal(sessionStorage.getItem("kiln.apiToken"), null);
  assert.equal(readAccount(), null);
  assert.equal(location.pathname, "/login");
  assert.deepEqual(assigned, ["/login"]);
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY), null);
  assert.equal(sessionStorage.getItem(CHAT_MODEL_KEY), null);
});

test("logout followed by a failed login still removes the old api token", async () => {
  const page = await mountLogin();
  take("/api/v1/auth/sync").resolveWith({ json: { configured: false } });
  await settled();
  writeApiToken("old-token");
  writeAccount({ id: "user-1", username: "ada", role_ids: ["qa"] });
  location.pathname = "/chat";
  assigned.length = 0;
  const loggingOut = logoutSession(requestJson);
  const logoutCall = take("/api/v1/auth/logout", "POST");
  submitLoginForm();
  await settled();
  assert.equal(logoutCall.options.signal.aborted, false);
  logoutCall.resolveWith({ status: 204, text: "" });
  await loggingOut;
  await settled();
  assert.equal(readApiToken(), "old-token");
  const failed = take("/api/v1/auth/login", "POST");
  failed.resolveWith({ status: 401, json: { detail: "口令不正确" } });
  await settled();
  assert.equal(readApiToken(), "");
  assert.equal(sessionStorage.getItem("kiln.apiToken"), null);
  assert.equal(readAccount(), null);
  assert.equal(document.body.textContent.includes("口令不正确"), true);
  assert.equal(location.pathname, "/login");
  assert.deepEqual(assigned, ["/login"]);
  page.app.unmount();
  apps.pop();
});

test("logout followed by a network failure on login still removes the old api token", async () => {
  await mountLogin();
  take("/api/v1/auth/sync").resolveWith({ json: { configured: false } });
  await settled();
  writeApiToken("old-token");
  writeAccount({ id: "user-1", username: "ada", role_ids: ["qa"] });
  location.pathname = "/chat";
  assigned.length = 0;
  const loggingOut = logoutSession(requestJson);
  const logoutCall = take("/api/v1/auth/logout", "POST");
  submitLoginForm();
  await settled();
  logoutCall.resolveWith({ status: 204, text: "" });
  await loggingOut;
  await settled();
  const dropped = take("/api/v1/auth/login", "POST");
  dropped.rejectWith(new TypeError("socket hang up"));
  await settled();
  assert.equal(readApiToken(), "");
  assert.equal(readAccount(), null);
  assert.equal(document.body.textContent.includes("无法连接 API"), true);
  assert.equal(location.pathname, "/login");
});

test("unmounting a login that replaced logout removes the old api token", async () => {
  const page = await mountLogin();
  take("/api/v1/auth/sync").resolveWith({ json: { configured: false } });
  await settled();
  writeApiToken("old-token");
  writeAccount({ id: "user-1", username: "ada", role_ids: ["qa"] });
  location.pathname = "/chat";
  assigned.length = 0;
  const loggingOut = logoutSession(requestJson);
  const logoutCall = take("/api/v1/auth/logout", "POST");
  submitLoginForm();
  await settled();
  logoutCall.resolveWith({ status: 204, text: "" });
  await loggingOut;
  await settled();
  const hanging = take("/api/v1/auth/login", "POST");
  assert.equal(readApiToken(), "old-token");
  page.app.unmount();
  apps.pop();
  assert.equal(readApiToken(), "");
  assert.equal(readAccount(), null);
  assert.equal(location.pathname, "/login");
  hanging.resolveWith({
    json: { account: { id: "user-8", username: "ghost", role_ids: ["reader"] } },
  });
  await settled();
  assert.equal(readAccount(), null);
  assert.equal(readApiToken(), "");
});

test("a successful login after logout keeps the account and removes the old bearer", async () => {
  await mountLogin();
  take("/api/v1/auth/sync").resolveWith({ json: { configured: false } });
  await settled();
  writeApiToken("old-token");
  location.pathname = "/chat";
  assigned.length = 0;
  const loggingOut = logoutSession(requestJson);
  const logoutCall = take("/api/v1/auth/logout", "POST");
  submitLoginForm();
  await settled();
  logoutCall.resolveWith({ status: 204, text: "" });
  await loggingOut;
  await settled();
  assert.equal(readApiToken(), "old-token");
  const succeeded = take("/api/v1/auth/login", "POST");
  succeeded.resolveWith({
    json: { account: { id: "user-2", username: "bea", role_ids: ["reader"] } },
  });
  await settled();
  assert.equal(readAccount().username, "bea");
  assert.equal(readApiToken(), "");
  assert.equal(sessionStorage.getItem("kiln.apiToken"), null);
  assert.equal(assigned.length, 0);
  assert.equal(location.pathname, "/chat");
});

test("replaceApiToken during a pending logout keeps the replacement bearer", async () => {
  writeApiToken("old-token");
  writeAccount({ id: "user-1", username: "ada", role_ids: ["qa"] });
  location.pathname = "/chat";
  assigned.length = 0;
  const loggingOut = logoutSession(requestJson);
  const logoutCall = take("/api/v1/auth/logout", "POST");
  replaceApiToken("replacement-bearer");
  assert.equal(logoutCall.options.signal.aborted, true);
  assert.equal(readApiToken(), "replacement-bearer");
  assert.equal(readAccount(), null);
  logoutCall.resolveWith({ status: 204, text: "" });
  await loggingOut;
  assert.equal(readApiToken(), "replacement-bearer");
  assert.equal(sessionStorage.getItem("kiln.apiToken"), "replacement-bearer");
  assert.equal(readAccount(), null);
});

test("a successful login without logout leaves the stored api token", async () => {
  await mountLogin();
  take("/api/v1/auth/sync").resolveWith({ json: { configured: false } });
  await settled();
  writeApiToken("kept-token");
  submitLoginForm();
  await settled();
  take("/api/v1/auth/login", "POST").resolveWith({
    json: { account: { id: "user-2", username: "bea", role_ids: ["reader"] } },
  });
  await settled();
  assert.equal(readAccount().username, "bea");
  assert.equal(readApiToken(), "kept-token");
});

test("a failed login without logout leaves the stored api token", async () => {
  await mountLogin();
  take("/api/v1/auth/sync").resolveWith({ json: { configured: false } });
  await settled();
  writeApiToken("kept-token");
  writeAccount({ id: "user-1", username: "ada", role_ids: ["qa"] });
  submitLoginForm();
  await settled();
  take("/api/v1/auth/login", "POST").resolveWith({ status: 500, json: { detail: "登录失败" } });
  await settled();
  assert.equal(readApiToken(), "kept-token");
  assert.equal(readAccount().username, "ada");
  assert.equal(document.body.textContent.includes("登录失败"), true);
});

function readerMe() {
  return {
    permissions: ["skill.read"],
    account: { id: "user-9", username: "reader-new", display_name: "Reader", role_ids: ["reader"] },
  };
}

async function finishAccessRefresh(me) {
  take("/api/v1/auth/me").resolveWith({ json: me });
  await settled();
  take("/api/v1/rbac/roles").resolveWith({
    json: { items: [{ id: "reader", name: "读者", permissions: ["skill.read"] }] },
  });
  await settled();
  take("/api/v1/rbac/permissions").resolveWith({
    json: { items: [{ code: "skill.read", description: "读取 Skill" }] },
  });
  await settled();
}

test("a cached login does not keep the previous account's page access", async () => {
  const { h } = await import("vue");
  const { createMemoryHistory, createRouter, RouterView } = await import("vue-router");
  const { allowRoute } = await import("./router/guard.js");
  const { canSee } = await import("./permissions.js");
  const Login = await loadLoginComponent();
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/login", name: "login", component: Login },
      {
        path: "/",
        name: "overview",
        component: {
          render() {
            const name = account.value?.username || "";
            const chat = accessReady.value && canSee("chat", perms.value);
            return h("p", { id: "after-login" }, `${name} chat=${chat}`);
          },
        },
      },
    ],
  });
  router.beforeEach(allowRoute);
  await router.push("/login");
  await router.isReady();
  const root = document.createElement("div");
  document.body.appendChild(root);
  const { createApp } = await import("vue");
  const app = createApp({ render: () => h(RouterView) });
  app.use(router);
  app.mount(root);
  apps.push(app);
  await nextTick();
  take("/api/v1/auth/sync").resolveWith({ json: { configured: false } });
  await settled();
  assert.equal(count("/api/v1/auth/me"), 0);
  assert.equal(account.value.username, "ada");
  assert.equal(perms.value.has("agent.qa"), true);
  submitLoginForm();
  await settled();
  take("/api/v1/auth/login", "POST").resolveWith({
    json: { account: readerMe().account },
  });
  await settled();
  assert.equal(count("/api/v1/auth/me"), 1);
  assert.equal(account.value, null);
  assert.equal(perms.value.has("agent.qa"), false);
  await finishAccessRefresh(readerMe());
  for (let step = 0; step < 8; step += 1) {
    await nextTick();
  }
  await router.replace({ name: "overview" });
  await nextTick();
  assert.equal(readAccount().username, "reader-new");
  assert.equal(account.value.username, "reader-new");
  assert.equal(perms.value.has("agent.qa"), false);
  assert.equal(accessReady.value, true);
  const guarded = document.body.querySelector("#after-login");
  assert.ok(
    guarded,
    `${router.currentRoute.value.fullPath} ready=${accessReady.value} account=${account.value?.username} qa=${perms.value?.has("agent.qa")} body=${document.body.textContent.slice(0, 80)}`,
  );
  assert.equal(guarded.textContent, "reader-new chat=false");
});

test("a roles response already in flight cannot restore the previous permissions", async () => {
  accessReady.value = false;
  const first = ensureAccess();
  take("/api/v1/auth/me").resolveWith({
    json: {
      permissions: ["skill.read"],
      account: { id: "user-1", username: "ada", display_name: "Ada", role_ids: ["qa"] },
    },
  });
  await settled();
  const oldRoles = take("/api/v1/rbac/roles");
  acceptLogin({ id: "user-9", username: "reader-new", display_name: "Reader", role_ids: ["reader"] });
  const second = ensureAccess();
  const freshMe = take("/api/v1/auth/me");
  oldRoles.resolveWith({
    json: { items: [{ id: "qa", name: "QA", permissions: ["agent.qa", "account.manage"] }] },
  });
  await settled();
  const oldLabels = calls.findLast((call) => call.url === "/api/v1/rbac/permissions" && !call.taken);
  if (oldLabels) {
    oldLabels.taken = true;
    oldLabels.rejectWith(Object.assign(new Error("旧权限列表失败"), { status: 500 }));
  }
  await Promise.resolve(first).catch(() => {});
  assert.equal(readAccount().username, "reader-new");
  assert.equal(accessReady.value, false);
  assert.equal(perms.value.has("agent.qa"), false);
  assert.equal(freshMe.settled, false);
  freshMe.resolveWith({ json: readerMe() });
  await settled();
  take("/api/v1/rbac/roles").resolveWith({
    json: { items: [{ id: "reader", name: "读者", permissions: ["skill.read"] }] },
  });
  await settled();
  take("/api/v1/rbac/permissions").resolveWith({
    json: { items: [{ code: "skill.read", description: "读取 Skill" }] },
  });
  await second;
  assert.equal(account.value.username, "reader-new");
  assert.equal(perms.value.has("agent.qa"), false);
  assert.equal(perms.value.has("skill.read"), true);
});

test("a permissions response already in flight cannot restore the previous permissions", async () => {
  accessReady.value = false;
  const first = ensureAccess();
  take("/api/v1/auth/me").resolveWith({
    json: {
      permissions: ["skill.read"],
      account: { id: "user-1", username: "ada", display_name: "Ada", role_ids: ["qa"] },
    },
  });
  await settled();
  take("/api/v1/rbac/roles").resolveWith({
    json: { items: [{ id: "qa", name: "QA", permissions: ["agent.qa"] }] },
  });
  await settled();
  const oldLabels = take("/api/v1/rbac/permissions");
  acceptLogin({ id: "user-9", username: "reader-new", display_name: "Reader", role_ids: ["reader"] });
  const second = ensureAccess();
  const freshMe = take("/api/v1/auth/me");
  oldLabels.resolveWith({
    json: { items: [{ code: "agent.qa", description: "问答" }, { code: "account.manage", description: "账号" }] },
  });
  await Promise.resolve(first).catch(() => {});
  assert.equal(readAccount().username, "reader-new");
  assert.equal(accessReady.value, false);
  assert.equal(perms.value.has("agent.qa"), false);
  assert.equal(perms.value.has("account.manage"), false);
  assert.equal(freshMe.settled, false);
  freshMe.resolveWith({ json: readerMe() });
  await settled();
  take("/api/v1/rbac/roles").resolveWith({
    json: { items: [{ id: "reader", name: "读者", permissions: ["skill.read"] }] },
  });
  await settled();
  take("/api/v1/rbac/permissions").resolveWith({
    json: { items: [{ code: "skill.read", description: "读取 Skill" }] },
  });
  await second;
  assert.equal(account.value.username, "reader-new");
  assert.equal(perms.value.has("skill.read"), true);
  assert.equal(perms.value.has("agent.qa"), false);
});

test("a late auth/me cannot restore the previous account or permissions", async () => {
  accessReady.value = false;
  const first = ensureAccess();
  const oldMe = take("/api/v1/auth/me");
  acceptLogin({ id: "user-9", username: "reader-new", display_name: "Reader", role_ids: ["reader"] });
  const second = ensureAccess();
  const freshMe = take("/api/v1/auth/me");
  assert.notEqual(oldMe, freshMe);
  oldMe.resolveWith({
    json: {
      permissions: ["agent.qa", "account.manage"],
      account: { id: "user-1", username: "ada", display_name: "Ada", role_ids: ["qa"] },
    },
  });
  await settled();
  const staleRoles = calls.findLast((call) => call.url === "/api/v1/rbac/roles" && !call.taken);
  if (staleRoles) {
    staleRoles.taken = true;
    staleRoles.resolveWith({
      json: { items: [{ id: "qa", name: "QA", permissions: ["agent.qa", "account.manage"] }] },
    });
    await settled();
  }
  const staleLabels = calls.findLast((call) => call.url === "/api/v1/rbac/permissions" && !call.taken);
  if (staleLabels) {
    staleLabels.taken = true;
    staleLabels.resolveWith({ json: { items: [{ code: "agent.qa", description: "问答" }] } });
    await settled();
  }
  await Promise.resolve(first).catch(() => {});
  assert.equal(readAccount().username, "reader-new");
  assert.equal(account.value, null);
  assert.equal(accessReady.value, false);
  assert.equal(perms.value.has("agent.qa"), false);
  assert.equal(freshMe.settled, false);
  freshMe.resolveWith({ json: readerMe() });
  await settled();
  take("/api/v1/rbac/roles").resolveWith({
    json: { items: [{ id: "reader", name: "读者", permissions: ["skill.read"] }] },
  });
  await settled();
  take("/api/v1/rbac/permissions").resolveWith({
    json: { items: [{ code: "skill.read", description: "读取 Skill" }] },
  });
  await second;
  assert.equal(account.value.username, "reader-new");
  assert.equal(perms.value.has("agent.qa"), false);
  assert.equal(perms.value.has("skill.read"), true);
  assert.equal(accessReady.value, true);
});

test("a failed access refresh cannot clear the refresh that replaced it", async () => {
  accessReady.value = false;
  const first = ensureAccess();
  const oldMe = take("/api/v1/auth/me");
  acceptLogin({ id: "user-9", username: "reader-new", display_name: "Reader", role_ids: ["reader"] });
  const second = ensureAccess();
  const freshMe = take("/api/v1/auth/me");
  const meCount = count("/api/v1/auth/me");
  const third = ensureAccess();
  assert.equal(third, second);
  assert.equal(count("/api/v1/auth/me"), meCount);
  oldMe.resolveWith({ status: 500, json: { detail: "旧访问失败" } });
  await settled();
  await Promise.resolve(first).catch(() => {});
  assert.equal(freshMe.settled, false);
  assert.equal(ensureAccess(), second);
  assert.equal(count("/api/v1/auth/me"), meCount);
  assert.equal(accessReady.value, false);
  assert.equal(perms.value.has("agent.qa"), false);
  freshMe.resolveWith({ json: readerMe() });
  await settled();
  take("/api/v1/rbac/roles").resolveWith({
    json: { items: [{ id: "reader", name: "读者", permissions: ["skill.read"] }] },
  });
  await settled();
  take("/api/v1/rbac/permissions").resolveWith({
    json: { items: [{ code: "skill.read", description: "读取 Skill" }] },
  });
  await second;
  assert.equal(account.value.username, "reader-new");
  assert.equal(accessReady.value, true);
  assert.equal(perms.value.has("agent.qa"), false);
});

test("a current auth/me 401 still expires the identity", async () => {
  accessReady.value = false;
  location.pathname = "/chat";
  assigned.length = 0;
  const pending = ensureAccess();
  take("/api/v1/auth/me").resolveWith({ status: 401, json: { detail: "未登录" } });
  await assert.rejects(pending, (caught) => caught.status === 401 && caught.staleAuth !== true);
  assert.equal(readAccount(), null);
  assert.equal(accessReady.value, false);
  assert.equal(perms.value.has("agent.qa"), false);
  assert.equal(location.pathname, "/login");
  assert.deepEqual(assigned, ["/login"]);
});

test("a failed fresh access check does not open client gates", async () => {
  const { h } = await import("vue");
  const { createMemoryHistory, createRouter, RouterView } = await import("vue-router");
  const { allowRoute } = await import("./router/guard.js");
  const { canDo, canSee } = await import("./permissions.js");
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/login", name: "login", component: { render: () => h("p", "登录") } },
      {
        path: "/",
        name: "overview",
        component: {
          render() {
            const chat = accessReady.value && canSee("chat", perms.value);
            const send = accessReady.value && canDo("chat.send", perms.value);
            return h("p", { id: "after-login" }, `ready=${accessReady.value} chat=${chat} send=${send}`);
          },
        },
      },
    ],
  });
  router.beforeEach(allowRoute);
  await router.push("/login");
  await router.isReady();
  const root = document.createElement("div");
  document.body.appendChild(root);
  const { createApp } = await import("vue");
  const app = createApp({ render: () => h(RouterView) });
  app.use(router);
  app.mount(root);
  apps.push(app);
  await nextTick();
  replaceApiToken("replacement-bearer");
  assert.equal(readAccount(), null);
  assert.equal(accessReady.value, false);
  assert.equal(perms.value.has("agent.qa"), false);
  const opened = router.replace({ name: "overview" });
  await settled();
  take("/api/v1/auth/me").resolveWith({ status: 503, json: { detail: "身份暂不可用" } });
  await settled();
  take("/api/v1/rbac/roles").resolveWith({
    json: { items: [{ id: "qa", name: "QA", permissions: ["agent.qa", "account.manage"] }] },
  });
  await settled();
  take("/api/v1/rbac/permissions").resolveWith({
    json: { items: [{ code: "agent.qa", description: "问答" }, { code: "account.manage", description: "账号" }] },
  });
  await opened;
  await nextTick();
  assert.notEqual(perms.value, null);
  assert.equal(canDo("chat.send", perms.value), false);
  assert.equal(canSee("chat", perms.value), false);
  assert.equal(accessReady.value, false);
  assert.equal(document.body.querySelector("#after-login").textContent, "ready=false chat=false send=false");
  const beforeRetry = count("/api/v1/auth/me");
  const retry = ensureAccess();
  await settled();
  assert.equal(count("/api/v1/auth/me"), beforeRetry + 1);
  await finishAccessRefresh(readerMe());
  await retry;
  await nextTick();
  assert.equal(accessReady.value, true);
  assert.equal(account.value.username, "reader-new");
  assert.equal(perms.value.has("skill.read"), true);
  assert.equal(perms.value.has("agent.qa"), false);
  assert.equal(document.body.querySelector("#after-login").textContent, "ready=true chat=false send=false");
});

test("unmounting the chat page drops late success and error from the previous view", async () => {
  await mountChatView();
  await finishInitialLoad();
  const textarea = document.body.querySelector("textarea");
  textarea.value = "卸载前";
  textarea.dispatchEvent({ type: "input", preventDefault() {}, stopPropagation() {} });
  await nextTick();
  document.body.querySelector("form").dispatchEvent({
    type: "submit",
    preventDefault() {},
    stopPropagation() {},
  });
  await settled();
  const first = take("/api/v1/llm/chat", "POST");
  first.honorAbort = false;
  apps.at(-1).unmount();
  apps.pop();
  document.body.textContent = "";
  await mountChatView();
  await finishInitialLoad();
  await nextTick();
  assert.equal(document.body.textContent.includes("结果未知，请求已中断"), true);
  assert.equal(document.body.textContent.includes("卸载前"), true);
  first.resolveWith({ status: 500, json: { detail: "旧页面的失败" } });
  await settled();
  await nextTick();
  flushHeldChatPersist();
  assert.equal(document.body.textContent.includes("旧页面的失败"), false);
  assert.equal(document.body.textContent.includes("结果未知，请求已中断"), true);
  const again = document.body.querySelector("textarea");
  again.value = "新页面";
  again.dispatchEvent({ type: "input", preventDefault() {}, stopPropagation() {} });
  await nextTick();
  document.body.querySelector("form").dispatchEvent({
    type: "submit",
    preventDefault() {},
    stopPropagation() {},
  });
  await settled();
  const second = take("/api/v1/llm/chat", "POST");
  second.honorAbort = false;
  assert.equal(document.body.textContent.includes("新页面"), true);
  assert.equal(JSON.parse(second.options.body).messages.some((item) => item.content === "卸载前"), false);
  apps.at(-1).unmount();
  apps.pop();
  document.body.textContent = "";
  await mountChatView();
  await finishInitialLoad();
  await nextTick();
  second.resolveWith({ json: { content: "旧页面的回答" } });
  await settled();
  await nextTick();
  flushHeldChatPersist();
  assert.equal(document.body.textContent.includes("旧页面的回答"), false);
  assert.equal(document.body.textContent.includes("旧页面的失败"), false);
  assert.equal(readChatThread(sessionStorage).some((item) => item.content === "旧页面的回答"), false);
  assert.equal(readChatThread(sessionStorage).some((item) => item.interrupted === true), true);
});

test("a superseded status or sidebar 401 in the same generation does not log out", async () => {
  const session = openSession();
  await boot(session);
  session.messages.value = [{ role: "user", content: "留下" }];
  flushHeldChatPersist();
  const threadBefore = sessionStorage.getItem(CHAT_THREAD_KEY);
  const modelBefore = sessionStorage.getItem(CHAT_MODEL_KEY);
  const first = session.load();
  const firstProvider = take("/api/v1/llm/provider");
  const firstMine = take("/api/v1/skills?scope=mine");
  const firstUsable = take("/api/v1/skills?scope=usable");
  const second = session.load();
  const secondProvider = take("/api/v1/llm/provider");
  const secondMine = take("/api/v1/skills?scope=mine");
  const secondUsable = take("/api/v1/skills?scope=usable");
  assert.equal(firstProvider.options.signal.aborted, false);
  assert.equal(firstMine.options.signal.aborted, false);
  secondProvider.resolveWith({ json: OPENAI });
  secondMine.resolveWith({ json: { items: [{ id: SKILL_ID, name: "新的", version: "1", status: "draft" }] } });
  secondUsable.resolveWith({ json: { items: [] } });
  await second;
  firstProvider.resolveWith({ status: 401, json: { detail: "过期的模型状态" } });
  firstMine.resolveWith({ status: 401, json: { detail: "过期的侧栏" } });
  firstUsable.resolveWith({ json: { items: [] } });
  await first;
  await settled();
  flushHeldChatPersist();
  assert.equal(readAccount().username, "ada");
  assert.equal(assigned.length, 0);
  assert.equal(location.pathname, "/chat");
  assert.equal(session.messages.value[0].content, "留下");
  assert.equal(session.mineItems.value[0].name, "新的");
  assert.equal(session.mode.value, "ready");
  assert.equal(session.pageError.value, "");
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY), threadBefore);
  assert.equal(sessionStorage.getItem(CHAT_MODEL_KEY), modelBefore);

  const staleMine = session.loadMine();
  const staleMineCall = take("/api/v1/skills?scope=mine");
  const currentMine = session.loadMine();
  const currentMineCall = take("/api/v1/skills?scope=mine");
  assert.equal(staleMineCall.options.signal.aborted, false);
  currentMineCall.resolveWith({
    json: { items: [{ id: SKILL_ID, name: "仍是新的", version: "1", status: "draft" }] },
  });
  await currentMine;
  staleMineCall.resolveWith({ status: 401, json: { detail: "更早的侧栏" } });
  await staleMine;
  await settled();
  flushHeldChatPersist();
  assert.equal(readAccount().username, "ada");
  assert.equal(assigned.length, 0);
  assert.equal(session.mineItems.value[0].name, "仍是新的");
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY), threadBefore);

  const currentExpiry = getJson("/api/v1/builds");
  take("/api/v1/builds").resolveWith({ status: 401, json: { detail: "当前请求未登录" } });
  await assert.rejects(currentExpiry, (caught) => caught.staleAuth !== true && caught.status === 401);
  assert.equal(readAccount(), null);
  assert.deepEqual(assigned, ["/login"]);
});

test("the next login is sent after the previous auth response, and its cookie is the one kept", async () => {
  const page = await mountLogin();
  take("/api/v1/auth/sync").resolveWith({ json: { configured: false } });
  await settled();
  submitLoginForm();
  await settled();
  const olderLogin = take("/api/v1/auth/login", "POST");
  submitLoginForm();
  await settled();
  assert.equal(olderLogin.options.signal.aborted, false);
  olderLogin.resolveWith({
    json: { account: { id: "user-1", username: "ada", role_ids: ["qa"] } },
    setCookie: "kiln_session=stale-login; Path=/; HttpOnly",
  });
  await settled();
  assert.equal(olderLogin.cookieApplied, true);
  const newerLogin = take("/api/v1/auth/login", "POST");
  newerLogin.resolveWith({
    json: { account: { id: "user-2", username: "bea", role_ids: ["reader"] } },
    setCookie: "kiln_session=current-login; Path=/; HttpOnly",
  });
  for (let step = 0; step < 5; step += 1) {
    await nextTick();
  }
  assert.equal(cookieJar.get("kiln_session"), "current-login");
  assert.equal(readAccount().username, "bea");
  assert.deepEqual(page.pushes, ["/"]);

  const loggingOut = logoutSession(requestJson);
  const logoutCall = take("/api/v1/auth/logout", "POST");
  const replacement = await mountLogin();
  take("/api/v1/auth/sync").resolveWith({ json: { configured: false } });
  await settled();
  submitLoginForm();
  await settled();
  assert.equal(logoutCall.options.signal.aborted, false);
  logoutCall.resolveWith({
    status: 204,
    text: "",
    setCookie: "kiln_session=; Max-Age=0; Path=/; HttpOnly",
  });
  await loggingOut;
  await settled();
  const replacementLogin = take("/api/v1/auth/login", "POST");
  assert.equal(logoutCall.cookieApplied, true);
  assert.equal(assigned.length, 0);
  replacementLogin.resolveWith({
    json: { account: { id: "user-9", username: "bea", role_ids: ["reader"] } },
    setCookie: "kiln_session=replacement-login; Path=/; HttpOnly",
  });
  await settled();
  assert.equal(cookieJar.get("kiln_session"), "replacement-login");
  assert.equal(readAccount().username, "bea");
  assert.equal(readAccount().id, "user-9");
  assert.deepEqual(replacement.pushes, ["/"]);

  const racing = await mountLogin();
  take("/api/v1/auth/sync").resolveWith({ json: { configured: false } });
  await settled();
  submitLoginForm();
  await settled();
  const racingLogin = take("/api/v1/auth/login", "POST");
  racingLogin.honorAbort = false;
  replaceApiToken("credential-after-login");
  assert.equal(racingLogin.options.signal.aborted, true);
  racingLogin.resolveWith({
    json: { account: { id: "user-1", username: "ada", role_ids: ["qa"] } },
    setCookie: "kiln_session=replaced-login; Path=/; HttpOnly",
  });
  await settled();
  assert.equal(racingLogin.cookieApplied, false);
  assert.equal(cookieJar.get("kiln_session"), "replacement-login");
  assert.equal(readApiToken(), "credential-after-login");
  assert.equal(readAccount(), null);
  assert.equal(racing.pushes.length, 0);
});

async function mountApp() {
  const source = resolve(dirname(fileURLToPath(import.meta.url)), "App.vue");
  const component = (await import(pathToFileURL(compileVue(source)).href)).default;
  const { createApp, h } = await import("vue");
  const { createMemoryHistory, createRouter } = await import("vue-router");
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "overview", component: { render: () => h("div", "概览") } },
      { path: "/login", name: "login", component: { render: () => h("div", "登录") } },
      { path: "/chat", name: "chat", component: { render: () => h("div", "对话") } },
    ],
  });
  await router.push("/");
  await router.isReady();
  const root = document.createElement("div");
  document.body.appendChild(root);
  const app = createApp(component);
  app.use(router);
  app.mount(root);
  apps.push(app);
  await nextTick();
  const health = calls.findLast((call) => call.url === "/health" && !call.taken);
  if (health) {
    health.taken = true;
    health.resolveWith({ json: { status: "ok" } });
  }
  await settled();
  return app;
}

test("the developer token form clears chat before reload and a separate old 401 does not undo it", async () => {
  writeApiToken("credential-old");
  sessionStorage.setItem(CHAT_MODEL_KEY, "model-b");
  const session = openSession();
  await boot(session);
  session.prompt.value = "表单保存前";
  const sending = session.send();
  const chat = take("/api/v1/llm/chat", "POST");
  chat.honorAbort = false;
  const obsolete = getJson("/api/v1/executor");
  const obsoleteCall = take("/api/v1/executor");
  await mountApp();
  const input = document.body.querySelector("#api-token");
  assert.ok(input);
  input.value = "credential-from-form";
  input.dispatchEvent({ type: "input", preventDefault() {}, stopPropagation() {} });
  await nextTick();
  let seenAtReload = null;
  location.reload = function reload() {
    this.reloads += 1;
    seenAtReload = {
      thread: sessionStorage.getItem(CHAT_THREAD_KEY),
      model: sessionStorage.getItem(CHAT_MODEL_KEY),
      token: readApiToken(),
      account: readAccount(),
    };
  };
  input.parentNode.dispatchEvent({
    type: "submit",
    preventDefault() {},
    stopPropagation() {},
  });
  await nextTick();
  assert.equal(location.reloads, 1);
  assert.equal(seenAtReload.token, "credential-from-form");
  assert.equal(seenAtReload.thread, null);
  assert.equal(seenAtReload.model, null);
  assert.equal(seenAtReload.account, null);
  chat.resolveWith({ json: { content: "旧令牌回答" } });
  await sending;
  flushHeldChatPersist();
  writeAccount({ id: "user-1", username: "ada", role_ids: ["qa"] });
  const remounted = openSession();
  obsoleteCall.resolveWith({ status: 401, json: { detail: "旧令牌未登录" } });
  await assert.rejects(obsolete, (caught) => caught.staleAuth === true && caught.status === 401);
  flushHeldChatPersist();
  assert.equal(readApiToken(), "credential-from-form");
  assert.equal(readAccount().id, "user-1");
  assert.equal(assigned.length, 0);
  assert.equal(remounted.messages.value.length, 0);
  assert.equal(sessionStorage.getItem(CHAT_THREAD_KEY), null);
  assert.equal(readChatThread(sessionStorage).some((item) => item.content === "旧令牌回答"), false);
});

function chromeBinary() {
  for (const candidate of ["/usr/local/bin/google-chrome", "/usr/bin/google-chrome", "/usr/bin/chromium"]) {
    if (existsSync(candidate)) {
      return candidate;
    }
  }
  return "";
}

test("this document's fetch seam checks auth cookie order, not the Vue login screen", { skip: chromeBinary() === "" }, async (t) => {
  const pending = [];
  const server = createServer((req, res) => {
    const url = req.url.split("?")[0];
    if (url === "/hold" || url === "/logout") {
      pending.push({ url, res });
      return;
    }
    if (url === "/set") {
      res.setHeader("Set-Cookie", "kiln_session=arrived; Path=/; HttpOnly");
      res.end("{}");
      return;
    }
    if (url === "/fresh") {
      res.setHeader("Set-Cookie", "kiln_session=fresh-login; Path=/; HttpOnly");
      res.end("{}");
      return;
    }
    if (url === "/fresh-final") {
      res.setHeader("Set-Cookie", "kiln_session=fresh-final; Path=/; HttpOnly");
      res.end("{}");
      return;
    }
    if (url === "/who") {
      res.setHeader("Content-Type", "text/plain");
      res.end(req.headers.cookie || "");
      return;
    }
    res.end("ok");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  const profile = mkdtempSync(resolve(tmpdirPath(), "kiln-chrome-"));
  const chrome = spawn(chromeBinary(), [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-extensions",
    "--no-first-run",
    "--disable-background-networking",
    `--user-data-dir=${profile}`,
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=0",
    "about:blank",
  ], { stdio: ["ignore", "pipe", "pipe"] });
  try {
    const portText = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`chrome did not open a debugging port\n${output.slice(-500)}`)), 25000);
      let output = "";
      const take = (chunk) => {
        output = `${output}${chunk}`.slice(-4000);
        const match = output.match(/127\.0\.0\.1:([1-9]\d*)/);
        if (match) {
          clearTimeout(timer);
          resolve(match[1]);
        }
      };
      chrome.stdout.on("data", take);
      chrome.stderr.on("data", take);
      chrome.on("exit", (code) => {
        clearTimeout(timer);
        reject(new Error(`chrome exited before opening a debugging port (${code})\n${output.slice(-500)}`));
      });
    });
    const page = await new Promise((resolve, reject) => {
      const request = httpRequest(`http://127.0.0.1:${portText}/json/new?${encodeURIComponent(`http://127.0.0.1:${port}/`)}`, { method: "PUT" }, (response) => {
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("end", () => {
          try {
            resolve(JSON.parse(Buffer.concat(chunks).toString()));
          } catch (error) {
            reject(error);
          }
        });
      });
      request.setTimeout(8000, () => request.destroy(new Error("devtools target timed out")));
      request.on("error", reject);
      request.end();
    });
    const socket = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", reject, { once: true });
    });
    let messageId = 0;
    const evaluate = (expression) => new Promise((resolve, reject) => {
      const id = ++messageId;
      const onMessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.id !== id) {
          return;
        }
        socket.removeEventListener("message", onMessage);
        if (data.result?.exceptionDetails) {
          reject(new Error(JSON.stringify(data.result.exceptionDetails)));
          return;
        }
        resolve(data.result?.result?.value);
      };
      socket.addEventListener("message", onMessage);
      socket.send(JSON.stringify({
        id,
        method: "Runtime.evaluate",
        params: { expression, awaitPromise: true, returnByValue: true },
      }));
    });
    const origin = `http://127.0.0.1:${port}`;
    let href = "";
    for (let attempt = 0; attempt < 50 && !String(href).startsWith(origin); attempt += 1) {
      href = await evaluate("location.href");
      if (!String(href).startsWith(origin)) {
        await new Promise((resolve) => setTimeout(resolve, 20));
      }
    }
    assert.equal(String(href).startsWith(origin), true);
    const authFetch = (path) => `fetch("${path}", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: "{}", signal: window.authController.signal })`;
    await evaluate(`(() => { window.authController = new AbortController(); window.authResult = ${authFetch("/hold")}.then(() => "stored").catch((error) => error.name); return "started"; })()`);
    for (let attempt = 0; attempt < 50 && !pending.some((item) => item.url === "/hold"); attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    assert.equal(pending.some((item) => item.url === "/hold"), true);
    const early = await evaluate(`(() => { window.authController.abort(); return window.authResult; })()`);
    assert.equal(early, "AbortError");
    const heldLogin = pending.splice(0, pending.length);
    heldLogin.forEach((item) => {
      item.res.setHeader("Set-Cookie", "kiln_session=stale-login; Path=/; HttpOnly");
      item.res.end("{}");
    });
    const afterStale = await evaluate(`fetch("/who", { credentials: "include" }).then((response) => response.text())`);
    await evaluate(`(() => { window.authController = new AbortController(); window.logoutResult = ${authFetch("/logout")}.then(() => "stored").catch((error) => error.name); return "started"; })()`);
    for (let attempt = 0; attempt < 50 && !pending.some((item) => item.url === "/logout"); attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    assert.equal(pending.some((item) => item.url === "/logout"), true);
    const logoutAbort = await evaluate(`(() => { window.authController.abort(); return window.logoutResult; })()`);
    assert.equal(logoutAbort, "AbortError");
    await evaluate(`fetch("/set", { method: "POST", credentials: "include" })`);
    const afterFresh = await evaluate(`fetch("/who", { credentials: "include" }).then((response) => response.text())`);
    assert.equal(afterFresh, "kiln_session=arrived", `cookie after the aborted header was ${JSON.stringify(afterStale)}`);
    pending.splice(0, pending.length).forEach((item) => {
      item.res.setHeader("Set-Cookie", "kiln_session=; Max-Age=0; Path=/; HttpOnly");
      item.res.end("");
    });
    const afterLateDelete = await evaluate(`fetch("/who", { credentials: "include" }).then((response) => response.text())`);
    assert.equal(
      afterLateDelete,
      "kiln_session=arrived",
      `cookie after the aborted logout header was ${JSON.stringify(afterLateDelete)}; completed login saw ${JSON.stringify(afterFresh)}; aborted login saw ${JSON.stringify(afterStale)}`,
    );
    await evaluate(`(() => { window.authController = new AbortController(); window.lateLogin = fetch("/hold", { method: "POST", credentials: "include", signal: window.authController.signal }).then(() => "stored").catch((error) => error.name); return "started"; })()`);
    for (let attempt = 0; attempt < 50 && !pending.some((item) => item.url === "/hold"); attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    assert.equal(pending.some((item) => item.url === "/hold"), true);
    const lateAbort = await evaluate(`(() => { window.authController.abort(); return window.lateLogin; })()`);
    assert.equal(lateAbort, "AbortError");
    await evaluate(`fetch("/fresh", { method: "POST", credentials: "include" })`);
    const beforeLateLoginHeader = await evaluate(`fetch("/who", { credentials: "include" }).then((response) => response.text())`);
    assert.equal(beforeLateLoginHeader, "kiln_session=fresh-login");
    pending.splice(0, pending.length).forEach((item) => {
      item.res.setHeader("Set-Cookie", "kiln_session=stale-after; Path=/; HttpOnly");
      item.res.end("{}");
    });
    const afterLateLoginHeader = await evaluate(`fetch("/who", { credentials: "include" }).then((response) => response.text())`);
    t.diagnostic(`stale login header after a completed login read back ${JSON.stringify(afterLateLoginHeader)}`);
    assert.equal(
      afterLateLoginHeader === "kiln_session=fresh-login" || afterLateLoginHeader === "kiln_session=stale-after",
      true,
      `stale login header released after the newer login left ${JSON.stringify(afterLateLoginHeader)}`,
    );
    await evaluate(`(() => { window.ordered = fetch("/hold", { method: "POST", credentials: "include" }).then(() => "stored").catch((error) => error.name); return "started"; })()`);
    for (let attempt = 0; attempt < 50 && !pending.some((item) => item.url === "/hold"); attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    assert.equal(pending.some((item) => item.url === "/hold"), true);
    pending.splice(0, pending.length).forEach((item) => {
      item.res.setHeader("Set-Cookie", "kiln_session=stale-first; Path=/; HttpOnly");
      item.res.end("{}");
    });
    assert.equal(await evaluate("window.ordered"), "stored");
    await evaluate(`fetch("/fresh-final", { method: "POST", credentials: "include" })`);
    const ordered = await evaluate(`fetch("/who", { credentials: "include" }).then((response) => response.text())`);
    assert.equal(ordered, "kiln_session=fresh-final");
    socket.close();
  } finally {
    chrome.kill("SIGKILL");
    await new Promise((resolve) => {
      if (chrome.exitCode != null || chrome.signalCode != null) {
        resolve();
        return;
      }
      chrome.once("exit", resolve);
      setTimeout(resolve, 1000);
    });
    server.close();
    try {
      rmSync(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 50 });
    } catch {
      // A profile directory that is still busy does not change the cookie result.
    }
  }
});

function tmpdirPath() {
  return process.env.TMPDIR || "/tmp";
}

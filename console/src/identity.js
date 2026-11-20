/**
 * Identity-wide auth epoch, separate from the per-tab chat generation.
 * New conversation advances only the chat generation. Logout, a current 401,
 * and a successful login advance the epoch and must not be undone by a reset.
 */

import { clearChatStorage, writeChatThread } from "./chat.js";
import { clearAccount, writeAccount } from "./session.js";
import { writeApiToken } from "./token.js";

let authEpoch = 0;
let chatGeneration = 0;
let persistStamp = 0;
let viewSerial = 0;
let holdPersist = false;

const liveViews = new Set();
const controllers = new Set();
const identityListeners = new Set();
const persistJobs = [];
const heldFlushes = [];
const signalScopes = new WeakMap();

let loginViewSerial = 0;
let activeLoginView = 0;
let loginAttemptSerial = 0;
let activeLoginAttempt = 0;
let activeLoginAttemptView = 0;

export function currentAuthEpoch() {
  return authEpoch;
}

export function currentChatGeneration() {
  return chatGeneration;
}

function abortControllers({ keepAuth = false } = {}) {
  for (const entry of controllers) {
    if (keepAuth && authExchange && entry.controller === authExchange) {
      continue;
    }
    try {
      entry.controller.abort();
    } catch {
      // Already aborted controllers still leave the registry.
    }
    controllers.delete(entry);
  }
}

export function advanceChatGeneration() {
  chatGeneration += 1;
  abortControllers({ keepAuth: true });
  return chatGeneration;
}

export function beginIdentityBoundary({ keepAuth = false, keepAttempt = false } = {}) {
  authEpoch += 1;
  chatGeneration += 1;
  abortControllers({ keepAuth });
  if (!keepAuth && !keepAttempt) {
    activeLoginAttempt = 0;
  }
  for (const listener of [...identityListeners]) {
    try {
      listener();
    } catch {
      // One view cannot block logout or session expiry.
    }
  }
  // Jobs scheduled by the listeners captured the previous stamp.
  persistStamp += 1;
  return { authEpoch, chatGeneration };
}

export function onIdentityBoundary(listener) {
  identityListeners.add(listener);
  return () => identityListeners.delete(listener);
}

export function openChatView() {
  const viewToken = ++viewSerial;
  liveViews.add(viewToken);
  return {
    viewToken,
    isAlive() {
      return liveViews.has(viewToken);
    },
    close() {
      if (!liveViews.delete(viewToken)) {
        return;
      }
      advanceChatGeneration();
    },
  };
}

export function registerAbortController(controller, isCurrent = null) {
  const entry = { controller };
  signalScopes.set(controller.signal, {
    epoch: authEpoch,
    generation: chatGeneration,
    isCurrent,
  });
  controllers.add(entry);
  const drop = () => controllers.delete(entry);
  if (controller.signal.aborted) {
    drop();
    return () => {};
  }
  controller.signal.addEventListener("abort", drop, { once: true });
  return () => {
    controller.signal.removeEventListener("abort", drop);
    drop();
  };
}

export function expiryApplies(epochAtStart, signal) {
  if (epochAtStart !== authEpoch) {
    return false;
  }
  if (signal?.aborted) {
    return false;
  }
  if (signal && signalScopes.has(signal)) {
    const scope = signalScopes.get(signal);
    if (scope.generation !== chatGeneration || scope.epoch !== epochAtStart) {
      return false;
    }
    if (typeof scope.isCurrent === "function" && !scope.isCurrent()) {
      return false;
    }
  }
  return true;
}

let authExchangeSerial = 0;
let authExchange = null;
let authTail = Promise.resolve();
let authDepth = 0;
let credentialSerial = 0;
let logoutObligation = null;

export function beginAuthExchange() {
  const controller = new AbortController();
  authExchangeSerial += 1;
  const serial = authExchangeSerial;
  authExchange = controller;
  registerAbortController(controller, () => authExchangeSerial === serial);
  const blocked = authDepth > 0;
  authDepth += 1;
  const ready = authTail;
  let openGate = () => {};
  let opened = false;
  authTail = new Promise((resolve) => {
    openGate = resolve;
  });
  return {
    signal: controller.signal,
    ready,
    blocked,
    finish() {
      if (opened) {
        return;
      }
      opened = true;
      authDepth = Math.max(0, authDepth - 1);
      openGate();
    },
  };
}

function loginAttemptPending() {
  return activeLoginView !== 0 && activeLoginAttempt !== 0 && activeLoginAttemptView === activeLoginView;
}

export function hasActiveLoginAttempt() {
  return loginAttemptPending();
}

function installCredential() {
  credentialSerial += 1;
  logoutObligation = null;
}

function collectLogoutObligation() {
  if (logoutObligation == null) {
    return;
  }
  if (credentialSerial !== logoutObligation) {
    logoutObligation = null;
    return;
  }
  if (loginAttemptPending()) {
    return;
  }
  logoutObligation = null;
  writeApiToken("");
  clearAccount();
  if (window.location.pathname !== "/login") {
    window.location.assign("/login");
  }
}

function rememberLogout(snapshot) {
  if (credentialSerial !== snapshot) {
    logoutObligation = null;
    return;
  }
  logoutObligation = snapshot;
  collectLogoutObligation();
}

export function openLoginView() {
  loginViewSerial += 1;
  const viewId = loginViewSerial;
  activeLoginView = viewId;
  return {
    viewId,
    alive() {
      return activeLoginView === viewId;
    },
    close() {
      if (activeLoginView === viewId) {
        activeLoginView = 0;
      }
      if (activeLoginAttemptView === viewId) {
        activeLoginAttempt = 0;
        activeLoginAttemptView = 0;
      }
      collectLogoutObligation();
    },
  };
}

export function beginLoginAttempt(view) {
  loginAttemptSerial += 1;
  const id = loginAttemptSerial;
  if (view?.alive()) {
    activeLoginAttempt = id;
    activeLoginAttemptView = view.viewId;
  }
  return {
    id,
    current() {
      return Boolean(view?.alive()) && activeLoginAttempt === id;
    },
  };
}

export function finishLoginAttempt(attempt) {
  if (attempt && activeLoginAttempt === attempt.id) {
    activeLoginAttempt = 0;
    activeLoginAttemptView = 0;
  }
  collectLogoutObligation();
}

function enqueuePersistFlush() {
  if (holdPersist) {
    heldFlushes.push(flushChatPersist);
    return;
  }
  queueMicrotask(flushChatPersist);
}

export function scheduleChatPersist(viewToken, isAlive, readLive, storage) {
  const scheduled = {
    authEpoch,
    chatGeneration,
    persistStamp,
    viewToken,
  };
  persistJobs.push(() => {
    const nowEpoch = authEpoch;
    const nowGeneration = chatGeneration;
    const nowStamp = persistStamp;
    if (nowStamp !== scheduled.persistStamp) {
      return;
    }
    if (nowEpoch !== scheduled.authEpoch) {
      return;
    }
    if (nowGeneration !== scheduled.chatGeneration) {
      return;
    }
    if (typeof isAlive === "function" && !isAlive()) {
      return;
    }
    writeChatThread(storage, readLive());
  });
  enqueuePersistFlush();
}

export function flushChatPersist() {
  const jobs = persistJobs.splice(0, persistJobs.length);
  for (const job of jobs) {
    job();
  }
}

export function holdChatPersistForTests(held) {
  holdPersist = Boolean(held);
  if (!holdPersist) {
    flushHeldChatPersist();
  }
}

export function flushHeldChatPersist() {
  const runs = heldFlushes.splice(0, heldFlushes.length);
  for (const run of runs) {
    run();
  }
}

export function acceptLogin(account) {
  installCredential();
  beginIdentityBoundary({ keepAttempt: true });
  clearChatStorage(window.sessionStorage);
  clearChatStorage(window.localStorage);
  if (account && typeof account.username === "string" && account.username) {
    writeAccount(account);
  }
}

export async function logoutSession(requestJson) {
  beginIdentityBoundary();
  const exchange = beginAuthExchange();
  const credentials = credentialSerial;
  clearChatStorage(window.sessionStorage);
  clearChatStorage(window.localStorage);
  try {
    if (exchange.blocked) {
      await exchange.ready;
    }
    await requestJson("/api/v1/auth/logout", { method: "POST", signal: exchange.signal });
  } finally {
    exchange.finish();
    rememberLogout(credentials);
    if (exchange.signal.aborted) {
      return;
    }
  }
}

export function replaceApiToken(nextToken) {
  installCredential();
  beginIdentityBoundary();
  clearChatStorage(window.sessionStorage);
  clearChatStorage(window.localStorage);
  writeApiToken(typeof nextToken === "string" ? nextToken : "");
  clearAccount();
  window.location.reload();
}

export function resetChatRuntimeForTests() {
  authEpoch = 0;
  chatGeneration = 0;
  persistStamp = 0;
  viewSerial = 0;
  holdPersist = false;
  liveViews.clear();
  for (const entry of controllers) {
    try {
      entry.controller.abort();
    } catch {
      // Tests reset whatever was still in flight.
    }
  }
  controllers.clear();
  identityListeners.clear();
  persistJobs.length = 0;
  heldFlushes.length = 0;
  loginViewSerial = 0;
  activeLoginView = 0;
  loginAttemptSerial = 0;
  activeLoginAttempt = 0;
  authExchange?.abort();
  authExchange = null;
  authExchangeSerial = 0;
  authTail = Promise.resolve();
  authDepth = 0;
  credentialSerial = 0;
  logoutObligation = null;
  activeLoginAttemptView = 0;
}

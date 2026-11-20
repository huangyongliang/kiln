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

export function currentAuthEpoch() {
  return authEpoch;
}

export function currentChatGeneration() {
  return chatGeneration;
}

function abortControllers() {
  for (const entry of controllers) {
    try {
      entry.controller.abort();
    } catch {
      // Already aborted controllers still leave the registry.
    }
  }
  controllers.clear();
}

export function advanceChatGeneration() {
  chatGeneration += 1;
  abortControllers();
  return chatGeneration;
}

export function beginIdentityBoundary() {
  authEpoch += 1;
  chatGeneration += 1;
  abortControllers();
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

export function registerAbortController(controller) {
  const entry = { controller };
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
  beginIdentityBoundary();
  clearChatStorage(window.sessionStorage);
  clearChatStorage(window.localStorage);
  if (account && typeof account.username === "string" && account.username) {
    writeAccount(account);
  }
}

export async function logoutSession(requestJson) {
  beginIdentityBoundary();
  clearChatStorage(window.sessionStorage);
  clearChatStorage(window.localStorage);
  try {
    await requestJson("/api/v1/auth/logout", { method: "POST" });
  } finally {
    writeApiToken("");
    clearAccount();
    window.location.assign("/login");
  }
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
}

import { fieldMessage } from "./forms.js";
import { clearChatStorage } from "./chat.js";
import { beginIdentityBoundary, currentAuthEpoch } from "./identity.js";
import { clearAccount, readAccount, writeFlash } from "./session.js";
import { readApiToken } from "./token.js";

function errorDetail(body, status) {
  if (!body || body.detail == null) {
    return `请求失败（${status}）`;
  }
  if (typeof body.detail === "string") {
    return body.detail;
  }
  if (Array.isArray(body.detail)) {
    return body.detail.map((item) => fieldMessage(item)).join("；");
  }
  return `请求失败（${status}）`;
}

function isSessionExpiry(path, status) {
  if (status !== 401) {
    return false;
  }
  return !path.startsWith("/api/v1/auth/login") && !path.startsWith("/api/v1/auth/sync");
}

export function isAbortError(caught) {
  return Boolean(caught && caught.name === "AbortError");
}

function networkError(caught) {
  const error = new Error("无法连接 API");
  error.cause = caught;
  return error;
}

function applyCurrentExpiry(epochAtStart) {
  if (epochAtStart !== currentAuthEpoch()) {
    return false;
  }
  beginIdentityBoundary();
  clearChatStorage(window.sessionStorage);
  clearChatStorage(window.localStorage);
  const hadAccount = Boolean(readAccount());
  clearAccount();
  if (window.location.pathname !== "/login") {
    if (hadAccount) {
      writeFlash("登录已过期，请重新登录");
    }
    window.location.assign("/login");
  }
  return true;
}

async function send(path, options = {}) {
  const token = readApiToken();
  const epochAtStart = currentAuthEpoch();
  const { signal, ...fetchOptions } = options;
  const rawBody = fetchOptions.body;
  const jsonBody = rawBody != null && !(rawBody instanceof Blob);
  let response;
  try {
    response = await fetch(path, {
      ...fetchOptions,
      ...(signal ? { signal } : {}),
      credentials: "include",
      headers: {
        Accept: "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(jsonBody ? { "Content-Type": "application/json" } : {}),
        ...(fetchOptions.headers || {}),
      },
    });
  } catch (caught) {
    if (isAbortError(caught)) {
      throw caught;
    }
    throw networkError(caught);
  }
  let text = "";
  try {
    text = await response.text();
  } catch (caught) {
    if (isAbortError(caught)) {
      throw caught;
    }
    throw networkError(caught);
  }
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }
  if (!response.ok) {
    const error = new Error(errorDetail(body, response.status));
    error.status = response.status;
    error.detail = body ? body.detail : null;
    if (isSessionExpiry(path, response.status)) {
      const current = applyCurrentExpiry(epochAtStart);
      if (!current) {
        error.staleAuth = true;
      }
    }
    throw error;
  }
  return { status: response.status, body };
}

export async function requestJson(path, options = {}) {
  const result = await send(path, options);
  return result.body;
}

export async function putBytes(path, blob, headers = {}) {
  const result = await send(path, {
    method: "PUT",
    body: blob,
    headers: {
      "Content-Type": "application/octet-stream",
      ...headers,
    },
  });
  return result;
}

export function getJson(path, options) {
  return requestJson(path, options || {});
}

export function putJson(path, payload) {
  return requestJson(path, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function postJson(path, payload, options = {}) {
  return requestJson(path, {
    ...options,
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function patchJson(path, payload) {
  return requestJson(path, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function postEmpty(path) {
  return requestJson(path, { method: "POST" });
}

export function errorText(caught, fallback) {
  if (caught instanceof Error && caught.message) {
    return caught.message;
  }
  return fallback;
}

export function isForbidden(caught) {
  return Boolean(caught && caught.status === 403);
}

export function isNotConfigured(caught) {
  return Boolean(caught && caught.status === 503);
}

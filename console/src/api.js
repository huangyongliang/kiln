import { fieldMessage } from "./forms";
import { clearAccount, readAccount, writeFlash } from "./session";
import { readApiToken } from "./token";

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

async function send(path, options = {}) {
  const token = readApiToken();
  const rawBody = options.body;
  const jsonBody = rawBody != null && !(rawBody instanceof Blob);
  let response;
  try {
    response = await fetch(path, {
      ...options,
      credentials: "include",
      headers: {
        Accept: "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(jsonBody ? { "Content-Type": "application/json" } : {}),
        ...(options.headers || {}),
      },
    });
  } catch (caught) {
    const error = new Error("无法连接 API");
    error.cause = caught;
    throw error;
  }
  const text = await response.text();
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
      const hadAccount = Boolean(readAccount());
      clearAccount();
      if (window.location.pathname !== "/login") {
        if (hadAccount) {
          writeFlash("登录已过期，请重新登录");
        }
        window.location.assign("/login");
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

export function getJson(path) {
  return requestJson(path);
}

export function putJson(path, payload) {
  return requestJson(path, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function postJson(path, payload) {
  return requestJson(path, {
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

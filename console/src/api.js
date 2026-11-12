import { clearAccount, writeFlash } from "./session";
import { readApiToken } from "./token";

function errorDetail(body, status) {
  if (!body || body.detail == null) {
    return `请求失败（${status}）`;
  }
  if (typeof body.detail === "string") {
    return body.detail;
  }
  if (Array.isArray(body.detail)) {
    return body.detail.map((item) => item.msg || "参数不正确").join("；");
  }
  return `请求失败（${status}）`;
}

function isSessionExpiry(path, status) {
  if (status !== 401) {
    return false;
  }
  return !path.startsWith("/api/v1/auth/login") && !path.startsWith("/api/v1/auth/sync");
}

export async function requestJson(path, options = {}) {
  const token = readApiToken();
  let response;
  try {
    response = await fetch(path, {
      ...options,
      credentials: "include",
      headers: {
        Accept: "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.body ? { "Content-Type": "application/json" } : {}),
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
    if (isSessionExpiry(path, response.status)) {
      clearAccount();
      if (window.location.pathname !== "/login") {
        writeFlash("登录已过期，请重新登录");
        window.location.assign("/login");
      }
    }
    throw error;
  }
  return body;
}

export function getJson(path) {
  return requestJson(path);
}

export function postJson(path, payload) {
  return requestJson(path, {
    method: "POST",
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

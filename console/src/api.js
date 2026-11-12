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

export async function requestJson(path, options = {}) {
  const token = readApiToken();
  const response = await fetch(path, {
    ...options,
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(errorDetail(body, response.status));
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

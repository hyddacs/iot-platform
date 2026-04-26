import { clearAuth, authState } from "./auth";

let authExpiredHandler = null;

export function setAuthExpiredHandler(handler) {
  authExpiredHandler = handler;
}

async function parseErrorMessage(response) {
  const text = await response.text();
  let message = `请求失败 (${response.status})`;

  try {
    const parsed = JSON.parse(text);
    return parsed.detail || parsed.message || message;
  } catch {
    return text || message;
  }
}

export async function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});

  if (authState.token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${authState.token}`);
  }

  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(path, {
    ...options,
    headers
  });

  if (response.status === 401) {
    if (authState.token && path !== "/api/auth/token") {
      clearAuth();
      if (authExpiredHandler) {
        authExpiredHandler();
      }
      throw new Error("登录状态已失效，请重新登录");
    }

    throw new Error(await parseErrorMessage(response));
  }

  if (!response.ok) {
    throw new Error(await parseErrorMessage(response));
  }

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }

  return response.text();
}

export function listItems(response) {
  return Array.isArray(response) ? response : response?.items || [];
}

export function toMessage(error) {
  return error instanceof Error ? error.message : "发生未知错误";
}

export function formatDateTime(value) {
  if (!value) {
    return "暂无";
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString("zh-CN", { hour12: false });
}

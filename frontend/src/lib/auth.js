import { reactive } from "vue";

const TOKEN_KEY = "iot-platform-token";
const USER_KEY = "iot-platform-user";

function readUser() {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) {
    return null;
  }

  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export const authState = reactive({
  token: localStorage.getItem(TOKEN_KEY) || "",
  user: readUser()
});

export function hasAuth() {
  return Boolean(authState.token && authState.user);
}

export function setAuth(token, user) {
  authState.token = token;
  authState.user = user;
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearAuth() {
  authState.token = "";
  authState.user = null;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

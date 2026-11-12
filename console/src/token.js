const STORAGE_KEY = "kiln.apiToken";

export function readApiToken() {
  return sessionStorage.getItem(STORAGE_KEY) || "";
}

export function writeApiToken(value) {
  const text = value.trim();
  if (text) {
    sessionStorage.setItem(STORAGE_KEY, text);
  } else {
    sessionStorage.removeItem(STORAGE_KEY);
  }
}

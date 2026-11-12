const ACCOUNT_KEY = "kiln.account";
const FLASH_KEY = "kiln.flash";

export function readAccount() {
  const raw = sessionStorage.getItem(ACCOUNT_KEY);
  if (!raw) {
    return null;
  }
  try {
    const value = JSON.parse(raw);
    if (value && typeof value === "object" && typeof value.username === "string") {
      return value;
    }
  } catch {
    sessionStorage.removeItem(ACCOUNT_KEY);
  }
  return null;
}

export function writeAccount(account) {
  if (!account || typeof account.username !== "string" || !account.username) {
    sessionStorage.removeItem(ACCOUNT_KEY);
    return;
  }
  sessionStorage.setItem(
    ACCOUNT_KEY,
    JSON.stringify({
      id: account.id,
      username: account.username,
      display_name: account.display_name || account.username,
      email: account.email || null,
      status: account.status || "",
      role_ids: Array.isArray(account.role_ids) ? account.role_ids : [],
    }),
  );
}

export function clearAccount() {
  sessionStorage.removeItem(ACCOUNT_KEY);
}

export function readFlash() {
  const text = sessionStorage.getItem(FLASH_KEY) || "";
  sessionStorage.removeItem(FLASH_KEY);
  return text;
}

export function writeFlash(text) {
  sessionStorage.setItem(FLASH_KEY, text);
}

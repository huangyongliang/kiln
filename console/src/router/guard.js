import { ensureAccess } from "../access.js";

export async function allowRoute(to) {
  if (to.name === "login") {
    return true;
  }
  try {
    await ensureAccess();
  } catch (caught) {
    if (caught?.status === 401) {
      // The request helper already starts a full navigation to /login and
      // stores the expiry notice. Abort this client navigation so the notice
      // is not consumed by a page that is about to unload.
      return false;
    }
  }
  return true;
}

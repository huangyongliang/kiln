import { computed, ref } from "vue";

import { getJson, isForbidden } from "./api";
import { permsFrom } from "./permissions";
import { clearAccount, readAccount, writeAccount } from "./session";

export const perms = ref(null);
export const account = ref(null);
export const roles = ref([]);
export const accessReady = ref(false);

export const roleTags = computed(() => {
  const current = account.value;
  if (!current) {
    return [];
  }
  const names = new Map(roles.value.map((role) => [role.id, role.name]));
  return (current.role_ids || []).map((id) => names.get(id)).filter(Boolean);
});

let pending = null;

export function loadAccess() {
  if (!pending) {
    pending = refreshAccess().finally(() => {
      pending = null;
    });
  }
  return pending;
}

async function refreshAccess() {
  accessReady.value = false;
  let me = null;
  try {
    me = await getJson("/api/v1/auth/me");
  } catch (caught) {
    if (isForbidden(caught) || !caught?.status || caught.status === 404) {
      me = null;
    } else if (caught.status === 401) {
      throw caught;
    } else {
      me = null;
    }
  }

  try {
    const body = await getJson("/api/v1/rbac/roles");
    roles.value = Array.isArray(body?.items) ? body.items : [];
  } catch (caught) {
    if (caught?.status === 401) {
      throw caught;
    }
    roles.value = [];
  }

  if (me && Array.isArray(me.permissions)) {
    const next = accountFromMe(me);
    if (next) {
      writeAccount(next);
    } else {
      clearAccount();
    }
    account.value = readAccount();
    perms.value = permsFrom(me);
    accessReady.value = true;
    return;
  }

  account.value = readAccount();
  if (!account.value || roles.value.length === 0) {
    perms.value = null;
    accessReady.value = true;
    return;
  }
  perms.value = permsFrom({ account: account.value, roles: roles.value });
  accessReady.value = true;
}

function accountFromMe(me) {
  if (me.account && typeof me.account.username === "string") {
    return me.account;
  }
  if (typeof me.username === "string" && Array.isArray(me.role_ids)) {
    return me;
  }
  return null;
}

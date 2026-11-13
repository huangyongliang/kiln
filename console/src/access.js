import { computed, ref } from "vue";

import { getJson } from "./api";
import { permsFrom } from "./permissions";
import { clearAccount, readAccount, writeAccount } from "./session";

export const perms = ref(null);
export const account = ref(null);
export const roles = ref([]);
export const permissionLabels = ref(new Map());
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

export function ensureAccess() {
  if (accessReady.value) {
    return Promise.resolve();
  }
  return loadAccess();
}

export function permissionDescription(code) {
  if (!code) {
    return "";
  }
  return permissionLabels.value.get(code) || code;
}

export function actionDeniedText(code) {
  return `没有权限（需要：${permissionDescription(code)}）`;
}

async function refreshAccess() {
  accessReady.value = false;
  let me = null;
  try {
    me = await getJson("/api/v1/auth/me");
  } catch (caught) {
    if (caught?.status === 401) {
      throw caught;
    }
    me = null;
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

  try {
    const body = await getJson("/api/v1/rbac/permissions");
    const items = Array.isArray(body?.items) ? body.items : [];
    permissionLabels.value = new Map(
      items
        .filter((item) => item && typeof item.code === "string")
        .map((item) => [item.code, item.description || item.code]),
    );
  } catch (caught) {
    if (caught?.status === 401) {
      throw caught;
    }
    permissionLabels.value = new Map();
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

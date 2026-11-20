import { computed, ref } from "vue";

import { getJson } from "./api.js";
import { currentAuthEpoch, onDurableIdentityBoundary } from "./identity.js";
import { permsFrom } from "./permissions.js";
import { clearAccount, readAccount, writeAccount } from "./session.js";

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
let refreshSerial = 0;

function closedPerms() {
  return new Set();
}

function invalidateAccess() {
  refreshSerial += 1;
  pending = null;
  accessReady.value = false;
  account.value = null;
  perms.value = closedPerms();
  roles.value = [];
  permissionLabels.value = new Map();
}

onDurableIdentityBoundary(invalidateAccess);

export function resetAccessForTests() {
  refreshSerial += 1;
  pending = null;
  accessReady.value = false;
  account.value = null;
  perms.value = null;
  roles.value = [];
  permissionLabels.value = new Map();
}

export function loadAccess() {
  if (!pending) {
    const serial = refreshSerial;
    const epoch = currentAuthEpoch();
    const job = refreshAccess(epoch, serial).finally(() => {
      if (pending === job && refreshSerial === serial) {
        pending = null;
      }
    });
    pending = job;
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

async function refreshAccess(epoch, serial) {
  const current = () => currentAuthEpoch() === epoch && refreshSerial === serial;
  if (!current()) {
    return;
  }
  accessReady.value = false;
  let me = null;
  try {
    me = await getJson("/api/v1/auth/me");
  } catch (caught) {
    if (caught?.status === 401) {
      throw caught;
    }
    if (!current()) {
      return;
    }
    me = null;
  }
  if (!current()) {
    return;
  }

  let nextRoles = [];
  try {
    const body = await getJson("/api/v1/rbac/roles");
    nextRoles = Array.isArray(body?.items) ? body.items : [];
  } catch (caught) {
    if (caught?.status === 401) {
      throw caught;
    }
    if (!current()) {
      return;
    }
    nextRoles = [];
  }
  if (!current()) {
    return;
  }

  let labels = new Map();
  try {
    const body = await getJson("/api/v1/rbac/permissions");
    const items = Array.isArray(body?.items) ? body.items : [];
    labels = new Map(
      items
        .filter((item) => item && typeof item.code === "string")
        .map((item) => [item.code, item.description || item.code]),
    );
  } catch (caught) {
    if (caught?.status === 401) {
      throw caught;
    }
    if (!current()) {
      return;
    }
    labels = new Map();
  }
  if (!current()) {
    return;
  }

  roles.value = nextRoles;
  permissionLabels.value = labels;
  if (me && Array.isArray(me.permissions)) {
    const next = accountFromMe(me);
    if (next) {
      writeAccount(next);
    } else {
      clearAccount();
    }
    account.value = readAccount();
    perms.value = permsFrom(me) || closedPerms();
    accessReady.value = true;
    return;
  }

  account.value = readAccount();
  if (!account.value || nextRoles.length === 0) {
    perms.value = account.value ? closedPerms() : null;
    accessReady.value = true;
    return;
  }
  perms.value = permsFrom({ account: account.value, roles: nextRoles }) || closedPerms();
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

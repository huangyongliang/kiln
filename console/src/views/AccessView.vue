<script setup>
import { computed, onMounted, ref, watch } from "vue";

import { accessReady, actionDeniedText, permissionDescription, perms, roles as catalogRoles } from "../access";
import { errorText, getJson, isForbidden, postJson } from "../api";
import FormDialog from "../components/FormDialog.vue";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import StatusTag from "../components/StatusTag.vue";
import {
  displayNameError,
  emailError,
  fieldErrors,
  passwordError,
  usernameError,
} from "../forms";
import { formatTime } from "../format";
import { canDo, canSee, GATE_DOCS } from "../permissions";

const lead = "账号与授权都在 kiln 内。公司系统接入用户信息同步后，只同步身份和在职状态。";
const note =
  "平台自身具备完整的账号与权限能力，账号和 RBAC 目录都在本地。现在有登录页，口令只保存哈希。公司用户系统的对接是可选的；可选对接之后，公司系统只同步身份和在职状态，kiln 的授权仍留在平台上。各操作所需权限见角色页签。";

const tab = ref("accounts");
const accounts = ref([]);
const permissions = ref([]);
const mode = ref("loading");
const error = ref("");
const forbidden = ref(false);
const openRole = ref("");
const createOpen = ref(false);
const createBusy = ref(false);
const createError = ref("");
const createErrors = ref({});
const username = ref("");
const displayName = ref("");
const email = ref("");
const password = ref("");
const roleIds = ref(["reader"]);

const roleNames = computed(() => {
  const names = new Map(catalogRoles.value.map((role) => [role.id, role.name]));
  return (ids) => (ids || []).map((id) => names.get(id)).filter(Boolean);
});

const showSource = computed(() =>
  accounts.value.some((item) => item && Object.prototype.hasOwnProperty.call(item, "source")),
);

const descriptions = computed(() => {
  const map = new Map(permissions.value.map((item) => [item.code, item.description]));
  return (code) => map.get(code) || permissionDescription(code);
});

onMounted(load);
watch(accessReady, (ready) => {
  if (ready) {
    load();
  }
});

async function load() {
  forbidden.value = false;
  if (accessReady.value && !canSee("access", perms.value)) {
    forbidden.value = true;
    return;
  }
  mode.value = "loading";
  error.value = "";
  try {
    const [accountBody, permissionBody] = await Promise.all([
      getJson("/api/v1/accounts"),
      getJson("/api/v1/rbac/permissions"),
    ]);
    accounts.value = accountBody.items || [];
    permissions.value = permissionBody.items || [];
    if (catalogRoles.value.length === 0) {
      const roleBody = await getJson("/api/v1/rbac/roles");
      catalogRoles.value = roleBody.items || [];
    }
    mode.value = "ready";
  } catch (caught) {
    if (isForbidden(caught)) {
      forbidden.value = true;
      return;
    }
    mode.value = "error";
    error.value = errorText(caught, "无法读取账号与权限");
  }
}

function toggleRole(id) {
  openRole.value = openRole.value === id ? "" : id;
}

function sourceText(value) {
  if (value === "local") {
    return "本地";
  }
  if (value === "sync") {
    return "用户信息同步";
  }
  return "—";
}

function openCreate() {
  username.value = "";
  displayName.value = "";
  email.value = "";
  password.value = "";
  roleIds.value = ["reader"];
  createError.value = "";
  createErrors.value = {};
  createOpen.value = true;
}

function closeCreate() {
  password.value = "";
  createOpen.value = false;
}

async function submitAccount() {
  if (createBusy.value) {
    return;
  }
  const local = {};
  const usernameMessage = usernameError(username.value);
  if (usernameMessage) {
    local.username = usernameMessage;
  }
  const displayMessage = displayNameError(displayName.value);
  if (displayMessage) {
    local.display_name = displayMessage;
  }
  const emailMessage = emailError(email.value);
  if (emailMessage) {
    local.email = emailMessage;
  }
  const passwordMessage = passwordError(password.value);
  if (passwordMessage) {
    local.password = passwordMessage;
  }
  if (roleIds.value.length === 0) {
    local.role_ids = "请选择角色";
  }
  createErrors.value = local;
  createError.value = "";
  if (Object.keys(local).length) {
    return;
  }
  const secret = password.value;
  password.value = "";
  const payload = {
    username: username.value,
    display_name: displayName.value.trim(),
    role_ids: [...roleIds.value],
  };
  const mail = email.value.trim();
  if (mail) {
    payload.email = mail;
  }
  if (secret) {
    payload.password = secret;
  }
  createBusy.value = true;
  try {
    const created = await postJson("/api/v1/accounts", payload);
    accounts.value = [created, ...accounts.value.filter((item) => item.id !== created.id)];
    closeCreate();
  } catch (caught) {
    if (isForbidden(caught)) {
      createError.value = actionDeniedText("account.manage");
      return;
    }
    const mapped = fieldErrors(caught?.detail);
    if (caught?.status === 409) {
      mapped.username = mapped.username || caught.message;
    }
    if (Object.keys(mapped).length) {
      createErrors.value = mapped;
      return;
    }
    createError.value = errorText(caught, "无法新建账号");
  } finally {
    createBusy.value = false;
  }
}
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="account.read" />
  <template v-else>
    <PageIntro title="账号与权限" :lead="lead" :note="note">
      <template v-if="canDo('account.create', perms)" #actions>
        <button type="button" class="btn" @click="openCreate">新建账号</button>
      </template>
    </PageIntro>
    <div class="tabs" role="tablist">
      <button type="button" class="chip" :class="{ active: tab === 'accounts' }" @click="tab = 'accounts'">
        账号
      </button>
      <button type="button" class="chip" :class="{ active: tab === 'roles' }" @click="tab = 'roles'">
        角色
      </button>
    </div>
    <section class="panel">
      <StatePanel
        :loading="mode === 'loading'"
        :error="mode === 'error' ? error : ''"
        :empty="mode === 'ready' && tab === 'accounts' && accounts.length === 0"
        empty-text="还没有账号。"
        @retry="load"
      >
        <div v-if="tab === 'accounts'" class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>用户名</th>
                <th>显示名</th>
                <th>角色</th>
                <th>邮箱</th>
                <th v-if="showSource">来源</th>
                <th>状态</th>
                <th>创建时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in accounts" :key="item.id">
                <td class="mono nowrap">{{ item.username }}</td>
                <td>{{ item.display_name }}</td>
                <td>
                  <span v-for="name in roleNames(item.role_ids)" :key="name" class="tag">{{ name }}</span>
                  <span v-if="roleNames(item.role_ids).length === 0" class="muted">—</span>
                </td>
                <td>{{ item.email || "—" }}</td>
                <td v-if="showSource">{{ sourceText(item.source) }}</td>
                <td class="nowrap">
                  <StatusTag :status="item.status" :label="item.status === 'active' ? '启用' : '停用'" />
                </td>
                <td class="nowrap">{{ formatTime(item.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>角色</th>
                <th>说明</th>
                <th>权限</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="role in catalogRoles" :key="role.id">
                <tr>
                  <td class="nowrap">{{ role.name }}</td>
                  <td>{{ role.description }}</td>
                  <td class="nowrap">
                    <button type="button" class="btn btn-ghost" @click="toggleRole(role.id)">
                      {{ openRole === role.id ? "收起" : "展开" }}
                    </button>
                  </td>
                </tr>
                <tr v-if="openRole === role.id">
                  <td colspan="3">
                    <div class="tag-wrap">
                      <span v-for="code in role.permissions" :key="code" class="tag">
                        {{ descriptions(code) }}
                      </span>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
          <h2 class="section-gap">操作与所需权限</h2>
          <table>
            <thead>
              <tr>
                <th>操作</th>
                <th>所需权限</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="gate in GATE_DOCS" :key="gate.action">
                <td class="nowrap">{{ gate.label }}</td>
                <td>
                  <div v-for="code in gate.codes" :key="code" class="perm-line">
                    {{ descriptions(code) }}
                    <span class="mono">{{ code }}</span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </StatePanel>
    </section>
    <FormDialog
      v-if="createOpen"
      title="新建账号"
      :busy="createBusy"
      :error="createError"
      @close="closeCreate"
      @submit="submitAccount"
    >
      <div class="field">
        <label for="new-username">用户名</label>
        <input id="new-username" v-model="username" type="text" autocomplete="off" />
        <p v-if="createErrors.username" class="field-error">{{ createErrors.username }}</p>
      </div>
      <div class="field">
        <label for="new-display">显示名</label>
        <input id="new-display" v-model="displayName" type="text" autocomplete="off" />
        <p v-if="createErrors.display_name" class="field-error">{{ createErrors.display_name }}</p>
      </div>
      <div class="field">
        <label for="new-email">邮箱</label>
        <input id="new-email" v-model="email" type="text" autocomplete="off" />
        <p v-if="createErrors.email" class="field-error">{{ createErrors.email }}</p>
      </div>
      <div class="field">
        <label for="new-password">口令</label>
        <input id="new-password" v-model="password" type="password" autocomplete="new-password" />
        <p class="hint">不填则不能用本地口令登录</p>
        <p v-if="createErrors.password" class="field-error">{{ createErrors.password }}</p>
      </div>
      <div class="field">
        <label>角色</label>
        <div class="checks">
          <label v-for="role in catalogRoles" :key="role.id" class="check">
            <input v-model="roleIds" type="checkbox" :value="role.id" />
            {{ role.name }}
          </label>
        </div>
        <p v-if="createErrors.role_ids" class="field-error">{{ createErrors.role_ids }}</p>
      </div>
    </FormDialog>
  </template>
</template>

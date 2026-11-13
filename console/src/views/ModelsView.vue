<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";

import { accessReady, perms } from "../access";
import { errorText, getJson, isForbidden, putJson } from "../api";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import { canDo, canSee } from "../permissions";

const ROLE_ORDER = ["reader", "builder", "publisher", "admin"];
const ID_MAX = 128;
const CATALOG_MAX = 32;
const ID_EMPTY = "模型 id 不能为空。";
const ID_TOO_LONG = "模型 id 不能超过 128 个字符，不会截断。";
const TOO_MANY = "模型目录不能超过 32 项，不会只保留前 32 项。";
const ID_DUPLICATE = "模型 id 重复。";
const NAME_TOO_LONG = "显示名不能超过 128 个字符。";

const lead = "维护平台模型目录，并把目录中的模型分配给固定角色。";
const note =
  "提供方、端点、密钥和超时仍由环境变量配置，此页不能修改，也不会显示密钥。尚未保存时，目录从 LLM_MODEL / LLM_MODELS 种子。保存后以本页为准，再改这两项环境变量不会覆盖。保存是整体替换，后一次写入生效，不使用 ETag。不从上游拉取模型目录。";

const source = ref("env");
const providerName = ref("");
const baseUrlConfigured = ref(false);
const apiKeyConfigured = ref(false);
const rows = ref([]);
const globalDefault = ref("");
const selected = reactive({ reader: [], builder: [], publisher: [], admin: [] });
const roleDefault = reactive({ reader: "", builder: "", publisher: "", admin: "" });
const roleCatalog = ref([]);
const draftId = ref("");
const draftName = ref("");
const mode = ref("loading");
const error = ref("");
const formError = ref("");
const saveError = ref("");
const saving = ref(false);
const forbidden = ref(false);

const canSave = computed(() => canDo("llm.catalog.update", perms.value));
const roleCards = computed(() =>
  ROLE_ORDER.map((id) => {
    const role = roleCatalog.value.find((item) => item && item.id === id);
    return {
      id,
      name: role?.name || id,
      chat: Array.isArray(role?.permissions) && role.permissions.includes("agent.qa"),
    };
  }),
);

onMounted(load);
watch(accessReady, (ready) => {
  if (ready) {
    load();
  }
});

async function load() {
  forbidden.value = false;
  if (accessReady.value && !canSee("models", perms.value)) {
    forbidden.value = true;
    return;
  }
  mode.value = "loading";
  error.value = "";
  try {
    const [catalog, roles] = await Promise.all([
      getJson("/api/v1/llm/catalog"),
      getJson("/api/v1/rbac/roles"),
    ]);
    roleCatalog.value = Array.isArray(roles?.items) ? roles.items : [];
    applyDocument(catalog);
    mode.value = "ready";
  } catch (caught) {
    if (isForbidden(caught)) {
      forbidden.value = true;
      return;
    }
    mode.value = "error";
    error.value = errorText(caught, "无法读取模型配置");
  }
}

function applyDocument(body) {
  source.value = body?.source === "db" ? "db" : "env";
  providerName.value = typeof body?.provider === "string" ? body.provider : "";
  baseUrlConfigured.value = Boolean(body?.base_url_configured);
  apiKeyConfigured.value = Boolean(body?.api_key_configured);
  const models = Array.isArray(body?.models) ? body.models : [];
  rows.value = models
    .filter((item) => item && typeof item.id === "string" && item.id)
    .map((item) => ({
      id: item.id,
      displayName: typeof item.display_name === "string" ? item.display_name : "",
    }));
  const ids = new Set(rows.value.map((row) => row.id));
  globalDefault.value = typeof body?.default_model === "string" && ids.has(body.default_model)
    ? body.default_model
    : "";
  const assignments = new Map(
    (Array.isArray(body?.assignments) ? body.assignments : [])
      .filter((item) => item && typeof item.role_id === "string")
      .map((item) => [item.role_id, item]),
  );
  for (const roleId of ROLE_ORDER) {
    const item = assignments.get(roleId);
    const chosen = Array.isArray(item?.models)
      ? item.models.filter((id) => typeof id === "string" && ids.has(id))
      : [];
    selected[roleId] = rows.value.map((row) => row.id).filter((id) => chosen.includes(id));
    const fallback = typeof item?.default_model === "string" ? item.default_model : "";
    roleDefault[roleId] = selected[roleId].includes(fallback) ? fallback : "";
  }
}

function addModel() {
  formError.value = "";
  const id = draftId.value.trim();
  const displayName = draftName.value.trim();
  if (!id) {
    formError.value = ID_EMPTY;
    return;
  }
  if (id.length > ID_MAX) {
    formError.value = ID_TOO_LONG;
    return;
  }
  if (displayName.length > ID_MAX) {
    formError.value = NAME_TOO_LONG;
    return;
  }
  if (rows.value.length >= CATALOG_MAX) {
    formError.value = TOO_MANY;
    return;
  }
  if (rows.value.some((row) => row.id === id)) {
    formError.value = ID_DUPLICATE;
    return;
  }
  rows.value = [...rows.value, { id, displayName }];
  if (!globalDefault.value) {
    globalDefault.value = id;
  }
  draftId.value = "";
  draftName.value = "";
}

function removeModel(id) {
  rows.value = rows.value.filter((row) => row.id !== id);
  if (globalDefault.value === id) {
    globalDefault.value = rows.value[0]?.id || "";
  }
  for (const roleId of ROLE_ORDER) {
    selected[roleId] = selected[roleId].filter((item) => item !== id);
    if (roleDefault[roleId] === id || !selected[roleId].includes(roleDefault[roleId])) {
      roleDefault[roleId] = "";
    }
  }
}

function moveModel(index, delta) {
  const next = index + delta;
  if (next < 0 || next >= rows.value.length) {
    return;
  }
  const copy = rows.value.slice();
  const [item] = copy.splice(index, 1);
  copy.splice(next, 0, item);
  rows.value = copy;
  const order = copy.map((row) => row.id);
  for (const roleId of ROLE_ORDER) {
    const have = new Set(selected[roleId]);
    selected[roleId] = order.filter((id) => have.has(id));
  }
}

function toggleRoleModel(roleId, id, checked) {
  const have = new Set(selected[roleId]);
  if (checked) {
    have.add(id);
  } else {
    have.delete(id);
  }
  selected[roleId] = rows.value.map((row) => row.id).filter((item) => have.has(item));
  if (!selected[roleId].includes(roleDefault[roleId])) {
    roleDefault[roleId] = "";
  }
}

function roleModels(roleId) {
  return selected[roleId];
}

async function save() {
  if (saving.value || !canSave.value) {
    return;
  }
  saveError.value = "";
  const ids = rows.value.map((row) => row.id);
  if (ids.length > CATALOG_MAX) {
    saveError.value = TOO_MANY;
    return;
  }
  if (new Set(ids).size !== ids.length) {
    saveError.value = ID_DUPLICATE;
    return;
  }
  const payload = {
    models: rows.value.map((row) => ({
      id: row.id,
      display_name: row.displayName.trim() || null,
    })),
    default_model: globalDefault.value || null,
    assignments: ROLE_ORDER.map((roleId) => ({
      role_id: roleId,
      models: [...selected[roleId]],
      default_model: roleDefault[roleId] || null,
    })),
  };
  saving.value = true;
  try {
    const saved = await putJson("/api/v1/llm/catalog", payload);
    applyDocument(saved);
  } catch (caught) {
    if (isForbidden(caught)) {
      saveError.value = "没有权限（需要：管理模型目录及角色可用模型）";
      return;
    }
    saveError.value = errorText(caught, "无法保存模型配置");
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="llm.manage" />
  <template v-else>
    <PageIntro title="模型配置" :lead="lead" :note="note">
      <template v-if="mode === 'ready' && canSave" #actions>
        <button type="button" class="btn" :disabled="saving" @click="save">
          {{ saving ? "正在保存" : "保存" }}
        </button>
      </template>
    </PageIntro>
    <StatePanel :loading="mode === 'loading'" :error="mode === 'error' ? error : ''" @retry="load">
      <section class="panel" aria-label="连接状态">
        <div class="panel-head">
          <h2>连接状态</h2>
          <span class="tag">{{ source === "db" ? "已保存" : "尚未保存" }}</span>
        </div>
        <dl class="model-status">
          <div>
            <dt>提供方</dt>
            <dd class="mono">{{ providerName || "—" }}</dd>
          </div>
          <div>
            <dt>端点</dt>
            <dd>{{ baseUrlConfigured ? "已配置" : "未配置" }}</dd>
          </div>
          <div>
            <dt>密钥</dt>
            <dd>{{ apiKeyConfigured ? "已配置" : "未配置" }}</dd>
          </div>
        </dl>
        <p class="hint">提供方、端点、密钥和超时仍由环境变量配置，此页不能修改，也不会显示密钥。</p>
        <p v-if="source === 'env'" class="hint">
          尚未保存。下面是环境变量 LLM_MODEL / LLM_MODELS 的种子，已有对话权限的角色可使用全部种子模型。
        </p>
        <p v-else class="hint">
          已保存。之后修改 LLM_MODEL 或 LLM_MODELS 不会覆盖这里的目录。保存是整体替换，后一次写入生效，不使用 ETag。
        </p>
      </section>

      <section class="panel" aria-label="模型目录">
        <div class="panel-head">
          <h2>模型目录</h2>
        </div>
        <form class="model-add" @submit.prevent="addModel">
          <label class="field">
            模型 id
            <input v-model="draftId" type="text" autocomplete="off" spellcheck="false" />
          </label>
          <label class="field">
            显示名
            <input v-model="draftName" type="text" autocomplete="off" />
          </label>
          <button type="submit" class="btn btn-secondary">添加</button>
        </form>
        <p v-if="formError" class="message error" role="alert">{{ formError }}</p>
        <p v-if="rows.length === 0" class="hint">目录为空。保存空目录后即视为已托管，对话不再转发未登记的模型 id。</p>
        <ul v-else class="model-list">
          <li v-for="(row, index) in rows" :key="row.id">
            <label class="check">
              <input
                type="radio"
                name="global-default"
                :value="row.id"
                :checked="globalDefault === row.id"
                @change="globalDefault = row.id"
              />
              全局默认
            </label>
            <span class="mono model-id">{{ row.id }}</span>
            <label class="field model-name-field">
              显示名
              <input v-model="row.displayName" type="text" autocomplete="off" />
            </label>
            <span class="model-order">
              <button type="button" class="btn btn-ghost" :disabled="index === 0" @click="moveModel(index, -1)">
                上移
              </button>
              <button
                type="button"
                class="btn btn-ghost"
                :disabled="index === rows.length - 1"
                @click="moveModel(index, 1)"
              >
                下移
              </button>
              <button type="button" class="btn btn-ghost" @click="removeModel(row.id)">删除</button>
            </span>
          </li>
        </ul>
      </section>

      <section class="panel" aria-label="按角色分配">
        <div class="panel-head">
          <h2>按角色分配</h2>
        </div>
        <div class="role-grid">
          <article v-for="role in roleCards" :key="role.id" class="role-card">
            <h3>
              {{ role.name }}
              <span class="mono">{{ role.id }}</span>
            </h3>
            <p v-if="!role.chat" class="hint">该角色无对话权限，分配不生效</p>
            <p v-if="rows.length === 0" class="hint">目录里还没有模型。</p>
            <ul v-else class="checks">
              <li v-for="row in rows" :key="row.id">
                <label class="check">
                  <input
                    type="checkbox"
                    :checked="roleModels(role.id).includes(row.id)"
                    @change="toggleRoleModel(role.id, row.id, $event.target.checked)"
                  />
                  <span class="mono">{{ row.id }}</span>
                  <span v-if="row.displayName">{{ row.displayName }}</span>
                </label>
              </li>
            </ul>
            <label class="field">
              角色默认
              <select v-model="roleDefault[role.id]" :disabled="roleModels(role.id).length === 0">
                <option value="">无</option>
                <option v-for="id in roleModels(role.id)" :key="id" :value="id">{{ id }}</option>
              </select>
            </label>
          </article>
        </div>
      </section>
      <p v-if="saveError" class="message error" role="alert">{{ saveError }}</p>
    </StatePanel>
  </template>
</template>

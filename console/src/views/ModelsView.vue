<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { onBeforeRouteLeave } from "vue-router";

import { accessReady, perms } from "../access";
import { errorText, getJson, isForbidden, putJson } from "../api";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import {
  ROLE_ORDER,
  SAVED_TEXT,
  catalogStatusTag,
  clearRowError,
  mapSaveFailure,
  prepareCatalogSave,
  rowErrorField,
} from "../models";
import {
  applyEdit,
  armBeforeUnload,
  clearDraftError,
  confirmRouteLeave,
  seedBannerFrom,
  validateNewModel,
} from "../modelsView";
import { canDo, canSee } from "../permissions";

const lead = "维护平台模型目录，并把目录中的模型分配给固定角色。";
const note =
  "提供方、端点、密钥和超时仍由环境变量配置，此页不能修改，也不会显示密钥。尚未保存时，目录从 LLM_MODEL / LLM_MODELS 种子。保存后以本页为准，再改这两项环境变量不会覆盖。保存是整体替换，后一次写入生效，不使用 ETag。不从上游拉取模型目录。";

const source = ref("env");
const seedBanner = ref("");
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
const draftError = ref(null);
const saveNotice = ref(null);
const saveRejected = ref(false);
const rowErrors = ref({});
const saveFeedback = ref(null);
const saving = ref(false);
const forbidden = ref(false);
const dirty = ref(false);

const canSave = computed(() => canDo("llm.catalog.update", perms.value));
const statusTag = computed(() => catalogStatusTag(source.value, saveRejected.value, dirty.value));
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

onMounted(() => {
  window.addEventListener("beforeunload", onBeforeUnload);
  load();
});
onBeforeUnmount(() => {
  window.removeEventListener("beforeunload", onBeforeUnload);
});
onBeforeRouteLeave(() => confirmRouteLeave(dirty.value, (message) => window.confirm(message)));
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
  saveNotice.value = null;
  saveRejected.value = false;
  draftError.value = null;
  rowErrors.value = {};
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
  seedBanner.value = seedBannerFrom(body);
  dirty.value = false;
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

function markDirty() {
  const next = applyEdit({ source: source.value });
  dirty.value = next.dirty;
  saveRejected.value = next.saveRejected;
  saveNotice.value = next.saveNotice;
}

function onBeforeUnload(event) {
  armBeforeUnload(dirty.value, event);
}

function onDraftInput(field) {
  draftError.value = clearDraftError(draftError.value, field);
  markDirty();
}

function addModel() {
  const result = validateNewModel(rows.value, draftId.value, draftName.value);
  if (!result.ok) {
    draftError.value = { field: result.field, message: result.message };
    return;
  }
  draftError.value = null;
  rows.value = [...rows.value, { id: result.id, displayName: result.displayName }];
  if (!globalDefault.value) {
    globalDefault.value = result.id;
  }
  draftId.value = "";
  draftName.value = "";
  markDirty();
}

function removeModel(id) {
  rows.value = rows.value.filter((row) => row.id !== id);
  markDirty();
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
  markDirty();
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
  markDirty();
}

function chooseDefault(id) {
  globalDefault.value = id;
  markDirty();
}

function rowField(index) {
  return rowErrorField(rowErrors.value[index]);
}

function onDisplayNameInput(index) {
  const next = clearRowError(rowErrors.value, index, "name");
  if (next !== rowErrors.value) {
    rowErrors.value = next;
  }
  markDirty();
}

function roleModels(roleId) {
  return selected[roleId];
}

function showSaveResult(kind, text) {
  saveNotice.value = { kind, text };
  revealSaveResult();
}

function revealSaveResult() {
  nextTick(() => {
    saveFeedback.value?.scrollIntoView({ block: "nearest", inline: "nearest" });
  });
}

function rejectSave(message, errors) {
  saveRejected.value = true;
  rowErrors.value = errors || {};
  showSaveResult("error", message);
}

async function save() {
  if (saving.value || !canSave.value) {
    return;
  }
  const prepared = prepareCatalogSave({
    rows: rows.value,
    globalDefault: globalDefault.value,
    assignments: selected,
    roleDefaults: roleDefault,
  });
  if (!prepared.ok) {
    rejectSave(prepared.message, prepared.rowErrors);
    return;
  }
  saving.value = true;
  try {
    const saved = await putJson("/api/v1/llm/catalog", prepared.body);
    applyDocument(saved);
    saveRejected.value = false;
    rowErrors.value = {};
    showSaveResult("ok", SAVED_TEXT);
  } catch (caught) {
    if (caught?.status === 422) {
      const mapped = mapSaveFailure(errorText(caught, "无法保存模型配置"), rows.value);
      rejectSave(mapped.message, mapped.rowErrors);
      return;
    }
    if (isForbidden(caught)) {
      rejectSave("没有权限（需要：管理模型目录及角色可用模型）", {});
      return;
    }
    rejectSave(errorText(caught, "无法保存模型配置"), {});
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
        <p
          v-if="saveNotice"
          ref="saveFeedback"
          class="save-note"
          :class="{ error: saveNotice.kind === 'error' }"
          :role="saveNotice.kind === 'error' ? 'alert' : 'status'"
        >
          {{ saveNotice.text }}
        </p>
        <button type="button" class="btn" :disabled="saving" @click="save">
          {{ saving ? "正在保存" : "保存" }}
        </button>
      </template>
    </PageIntro>
    <StatePanel :loading="mode === 'loading'" :error="mode === 'error' ? error : ''" @retry="load">
      <section class="panel" aria-label="连接状态">
        <div class="panel-head">
          <h2>连接状态</h2>
          <span class="tag" :class="{ fail: saveRejected, ok: !saveRejected && !dirty && source === 'db' }">
            {{ statusTag }}
          </span>
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
        <p v-if="seedBanner" class="banner warn" role="status">{{ seedBanner }}</p>
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
            <input
              v-model="draftId"
              type="text"
              autocomplete="off"
              spellcheck="false"
              :aria-invalid="draftError?.field === 'id'"
              @input="onDraftInput('id')"
            />
          </label>
          <label class="field">
            显示名
            <input
              v-model="draftName"
              type="text"
              autocomplete="off"
              :aria-invalid="draftError?.field === 'name'"
              @input="onDraftInput('name')"
            />
          </label>
          <button type="submit" class="btn btn-secondary">添加</button>
        </form>
        <p v-if="draftError" class="message error" role="alert">{{ draftError.message }}</p>
        <p v-if="rows.length === 0" class="hint">目录为空。保存空目录后即视为已托管，对话不再转发未登记的模型 id。</p>
        <ul v-else class="model-list">
          <li v-for="(row, index) in rows" :key="row.id">
            <label class="check">
              <input
                type="radio"
                name="global-default"
                :value="row.id"
                :checked="globalDefault === row.id"
                @change="chooseDefault(row.id)"
              />
              全局默认
            </label>
            <span class="model-id-field">
              <input
                class="mono model-id"
                type="text"
                readonly
                :value="row.id"
                :title="row.id"
                aria-label="模型 id"
                :aria-invalid="rowField(index) === 'id'"
              />
              <span v-if="rowField(index) === 'id'" class="message error">{{ rowErrors[index] }}</span>
            </span>
            <label class="field model-name-field">
              显示名
              <input
                v-model="row.displayName"
                type="text"
                autocomplete="off"
                :aria-invalid="rowField(index) === 'name'"
                @input="onDisplayNameInput(index)"
              />
              <span v-if="rowField(index) === 'name'" class="message error">{{ rowErrors[index] }}</span>
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
                  <span class="mono" :title="row.id">{{ row.id }}</span>
                  <span v-if="row.displayName" class="check-name">{{ row.displayName }}</span>
                </label>
              </li>
            </ul>
            <label class="field">
              角色默认
              <select v-model="roleDefault[role.id]" :disabled="roleModels(role.id).length === 0" @change="markDirty">
                <option value="">无</option>
                <option v-for="id in roleModels(role.id)" :key="id" :value="id">{{ id }}</option>
              </select>
            </label>
          </article>
        </div>
      </section>
    </StatePanel>
  </template>
</template>

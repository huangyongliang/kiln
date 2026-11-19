<script setup>
import { computed, onMounted, ref, watch } from "vue";

import { accessReady, perms } from "../access";
import { errorText, getJson, isForbidden, putJson, postJson, requestJson } from "../api";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import { RUNTIME_TOOLS, catalogFieldErrors, catalogPayload } from "../buildTypes";
import { fieldErrors } from "../forms";
import { canSee } from "../permissions";

const note =
  "构建类型和运行时条件由管理员维护。Skill 草稿绑定其中一条。Jenkins 标签留空时任务仍可在任意在线节点运行；填写后必须已有匹配的在线节点。被引用的类型不能删除，只能停用。";

const items = ref([]);
const mode = ref("loading");
const error = ref("");
const forbidden = ref(false);
const editing = ref("");
const code = ref("");
const label = ref("");
const description = ref("");
const enabled = ref(true);
const jenkinsLabel = ref("");
const conditions = ref([]);
const formError = ref("");
const localErrors = ref({});
const busy = ref(false);

const editingRow = computed(() => items.value.find((item) => item.code === editing.value) || null);
const formTitle = computed(() => (editing.value ? `编辑 ${editing.value}` : "新增构建类型"));

onMounted(load);
watch(accessReady, (ready) => {
  if (ready) {
    load();
  }
});

async function load() {
  forbidden.value = false;
  if (accessReady.value && !canSee("build-types", perms.value)) {
    forbidden.value = true;
    return;
  }
  mode.value = "loading";
  error.value = "";
  try {
    const body = await getJson("/api/v1/build-package-types?include_disabled=true");
    items.value = body.items || [];
    mode.value = "ready";
  } catch (caught) {
    if (isForbidden(caught)) {
      forbidden.value = true;
      return;
    }
    mode.value = "error";
    error.value = errorText(caught, "无法读取构建类型");
  }
}

function blankForm() {
  editing.value = "";
  code.value = "";
  label.value = "";
  description.value = "";
  enabled.value = true;
  jenkinsLabel.value = "";
  conditions.value = [];
  formError.value = "";
  localErrors.value = {};
}

function editRow(row) {
  editing.value = row.code;
  code.value = row.code;
  label.value = row.label;
  description.value = row.description || "";
  enabled.value = row.enabled !== false;
  jenkinsLabel.value = row.jenkins_label || "";
  conditions.value = (row.runtime_conditions || []).map((item) => ({
    tool: item.tool,
    version: item.version,
  }));
  formError.value = "";
  localErrors.value = {};
}

function addCondition() {
  if (conditions.value.length >= 4) {
    return;
  }
  const used = new Set(conditions.value.map((item) => item.tool));
  const tool = RUNTIME_TOOLS.find((item) => !used.has(item[0]));
  conditions.value.push({ tool: tool ? tool[0] : "jdk", version: "" });
}

async function save() {
  const errors = catalogFieldErrors({
    code: code.value,
    label: label.value,
    description: description.value,
    jenkinsLabel: jenkinsLabel.value,
    conditions: conditions.value,
    editing: Boolean(editing.value),
  });
  localErrors.value = errors;
  formError.value = "";
  if (Object.keys(errors).length || busy.value) {
    return;
  }
  const payload = catalogPayload({
    code: code.value,
    label: label.value,
    description: description.value,
    enabled: enabled.value,
    jenkinsLabel: jenkinsLabel.value,
    conditions: conditions.value,
  });
  busy.value = true;
  try {
    if (editing.value) {
      const { code: _code, ...body } = payload;
      await putJson(`/api/v1/build-package-types/${encodeURIComponent(editing.value)}`, body);
    } else {
      await postJson("/api/v1/build-package-types", payload);
    }
    blankForm();
    await load();
  } catch (caught) {
    if (isForbidden(caught)) {
      formError.value = caught.message;
      return;
    }
    const mapped = fieldErrors(caught?.detail);
    if (Object.keys(mapped).length) {
      localErrors.value = mapped;
      return;
    }
    formError.value = errorText(caught, "无法保存构建类型");
  } finally {
    busy.value = false;
  }
}

async function removeRow(row) {
  if (busy.value) {
    return;
  }
  busy.value = true;
  formError.value = "";
  try {
    await requestJson(`/api/v1/build-package-types/${encodeURIComponent(row.code)}`, {
      method: "DELETE",
    });
    if (editing.value === row.code) {
      blankForm();
    }
    await load();
  } catch (caught) {
    formError.value = errorText(caught, "无法删除构建类型");
  } finally {
    busy.value = false;
  }
}

function conditionText(row) {
  const conditions = row.runtime_conditions || [];
  if (!conditions.length) {
    return "无";
  }
  return conditions.map((item) => `${item.tool} ${item.version}`).join("，");
}
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="build.catalog.manage" />
  <template v-else>
    <PageIntro title="构建类型" lead="管理构建类型、运行时条件和 Jenkins 标签。" :note="note">
      <template #actions>
        <button type="button" class="btn btn-secondary" @click="blankForm">新增</button>
      </template>
    </PageIntro>
    <StatePanel
      :loading="mode === 'loading'"
      :error="mode === 'error' ? error : ''"
      @retry="load"
    >
      <section v-if="items.length" class="panel">
        <div class="panel-head">
          <h2>目录</h2>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>编码</th>
                <th>标签</th>
                <th>运行时</th>
                <th>Jenkins 标签</th>
                <th>状态</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in items" :key="row.code">
                <td class="mono">{{ row.code }}</td>
                <td>{{ row.label }}</td>
                <td>{{ conditionText(row) }}</td>
                <td class="mono">{{ row.jenkins_label || "—" }}</td>
                <td>{{ row.enabled ? "启用" : "停用" }}</td>
                <td class="nowrap">
                  <button type="button" class="btn btn-ghost" @click="editRow(row)">编辑</button>
                  <button type="button" class="btn btn-ghost" :disabled="busy" @click="removeRow(row)">
                    删除
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>{{ formTitle }}</h2>
        </div>
        <form @submit.prevent="save">
          <div class="field">
            <label for="type-code">编码</label>
            <input id="type-code" v-model="code" type="text" autocomplete="off" :disabled="Boolean(editing)" />
            <p v-if="localErrors.code" class="field-error">{{ localErrors.code }}</p>
          </div>
          <div class="field">
            <label for="type-label">标签</label>
            <input id="type-label" v-model="label" type="text" autocomplete="off" />
            <p v-if="localErrors.label" class="field-error">{{ localErrors.label }}</p>
          </div>
          <div class="field">
            <label for="type-description">说明</label>
            <input id="type-description" v-model="description" type="text" autocomplete="off" />
            <p v-if="localErrors.description" class="field-error">{{ localErrors.description }}</p>
          </div>
          <div class="field">
            <label for="type-jenkins">Jenkins 标签</label>
            <p class="field-note">留空表示不指定节点。只填写一个已有标签或节点名，不要写 &amp;&amp; 或 ||。</p>
            <input id="type-jenkins" v-model="jenkinsLabel" type="text" autocomplete="off" />
            <p v-if="localErrors.jenkins_label" class="field-error">{{ localErrors.jenkins_label }}</p>
          </div>
          <div class="field">
            <label>运行时条件</label>
            <div v-for="(row, index) in conditions" :key="index" class="repeat-row">
              <select v-model="row.tool">
                <option v-for="tool in RUNTIME_TOOLS" :key="tool[0]" :value="tool[0]">{{ tool[1] }}</option>
              </select>
              <input v-model="row.version" type="text" autocomplete="off" placeholder="版本，例如 17" />
              <button type="button" class="btn btn-ghost" @click="conditions.splice(index, 1)">删除</button>
              <p v-if="localErrors[`condition-${index}`]" class="field-error">
                {{ localErrors[`condition-${index}`] }}
              </p>
            </div>
            <button v-if="conditions.length < 4" type="button" class="btn btn-secondary" @click="addCondition">
              添加条件
            </button>
            <p v-if="localErrors.runtime_conditions" class="field-error">{{ localErrors.runtime_conditions }}</p>
          </div>
          <label class="check">
            <input v-model="enabled" type="checkbox" />
            启用
          </label>
          <p v-if="editingRow && !editingRow.enabled" class="hint">停用后不能绑到新草稿，已绑定的已发布 Skill 仍可构建。</p>
          <p v-if="formError" class="message error" role="alert">{{ formError }}</p>
          <div class="form-actions">
            <button type="submit" class="btn" :disabled="busy">{{ busy ? "正在保存" : "保存" }}</button>
          </div>
        </form>
      </section>
    </StatePanel>
  </template>
</template>

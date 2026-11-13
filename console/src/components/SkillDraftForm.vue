<script setup>
import { computed, reactive, ref, watch } from "vue";

import { accessReady, perms } from "../access";
import { getJson } from "../api";
import {
  boundError,
  inputNameError,
  scopeList,
  scopeListError,
  skillNameError,
  stepKindError,
  versionError,
} from "../forms";
import { canDo } from "../permissions";
import {
  DEPENDENCY_NOTE,
  REPO_MANUAL_UNRESTRICTED,
  REPO_NOTICES,
  REPO_UNRESTRICTED,
  allowedRepositoriesFromChecklist,
  checklistRepositoriesError,
  fetchGitCatalog,
  repositoryChecklist,
  repositoryFieldState,
  repositoryFullNames,
  repositoryLinesError,
} from "../repositories";
import PageIntro from "./PageIntro.vue";

const STEP_OPTIONS = [
  ["checkout", "检出"],
  ["compile", "编译"],
  ["package", "打包"],
  ["archive", "归档"],
];

const props = defineProps({
  initial: { type: Object, default: null },
  busy: { type: Boolean, default: false },
  submitLabel: { type: String, default: "提交" },
  title: { type: String, default: "" },
  lead: { type: String, default: "" },
  formError: { type: String, default: "" },
  serverErrors: { type: Object, default: () => ({}) },
  showCancel: { type: Boolean, default: false },
});

const emit = defineEmits(["submit", "cancel"]);

const errors = reactive({});
const repoMode = ref("loading");
const repoNotice = ref("");
const repoCatalog = ref([]);
const keptRepos = ref([]);
const selectedRepos = ref([]);
const repoManual = ref("");
const repoChoices = computed(() => repositoryChecklist(repoCatalog.value, keptRepos.value));
const form = reactive({
  name: "",
  version: "0.1.0",
  description: "",
  toolchain: "",
  environment: "",
  output_spec: "",
  allowed_repositories: "",
  allowed_dependencies: "",
});
const inputs = ref([]);
const steps = ref([{ kind: "checkout", description: "" }]);

const scalars = [
  ["name", "名称", "skill-name"],
  ["version", "版本", "skill-version"],
  ["description", "说明", "skill-description", 3],
  ["toolchain", "工具链", "skill-toolchain"],
  ["environment", "环境", "skill-environment"],
  ["output_spec", "输出说明", "skill-output", 2],
  ["allowed_repositories", "允许仓库", "skill-repos"],
  ["allowed_dependencies", "允许依赖", "skill-deps", 3, "一行一条", DEPENDENCY_NOTE],
];

watch(() => props.initial, applyInitial, { immediate: true });
watch(accessReady, (ready) => {
  if (ready) {
    loadRepositoryChoices();
  }
}, { immediate: true });

async function loadRepositoryChoices() {
  if (!accessReady.value) {
    return;
  }
  if (perms.value != null && !canDo("git.read", perms.value)) {
    useManual("forbidden");
    return;
  }
  const result = await fetchGitCatalog(getJson);
  if (result.problem) {
    useManual(result.problem);
    return;
  }
  const state = repositoryFieldState(result, scopeList(form.allowed_repositories));
  repoCatalog.value = repositoryFullNames(result.items);
  if (repoMode.value !== "checklist") {
    keptRepos.value = state.selected.slice();
    selectedRepos.value = state.selected.slice();
  }
  repoMode.value = "checklist";
  repoNotice.value = "";
}

function useManual(problem) {
  if (repoMode.value === "checklist") {
    form.allowed_repositories = allowedRepositoriesFromChecklist(
      selectedRepos.value,
      repoManual.value,
      repoChoices.value,
    ).join("\n");
    repoManual.value = "";
  }
  repoCatalog.value = [];
  repoMode.value = "manual";
  repoNotice.value = REPO_NOTICES[problem] || REPO_NOTICES.source;
}

function repositoryPayload() {
  if (repoMode.value === "checklist") {
    return allowedRepositoriesFromChecklist(selectedRepos.value, repoManual.value, repoChoices.value);
  }
  return scopeList(form.allowed_repositories);
}

function repositoryMessage() {
  if (repoMode.value === "checklist") {
    return checklistRepositoriesError(selectedRepos.value, repoManual.value, repoChoices.value);
  }
  return repositoryLinesError(form.allowed_repositories);
}

function applyInitial(skill) {
  form.name = skill?.name || "";
  form.version = skill?.version || "0.1.0";
  form.description = skill?.description || "";
  form.toolchain = skill?.toolchain || "";
  form.environment = skill?.environment || "";
  form.output_spec = skill?.output_spec || "";
  const repositories = (skill?.allowed_repositories || [])
    .map((item) => String(item ?? "").trim())
    .filter(Boolean);
  form.allowed_repositories = repositories.join("\n");
  keptRepos.value = repositories;
  selectedRepos.value = [...repositories];
  repoManual.value = "";
  form.allowed_dependencies = (skill?.allowed_dependencies || []).join("\n");
  inputs.value = (skill?.inputs || []).map((row) => ({
    name: row.name || "",
    description: row.description || "",
    required: row.required !== false,
  }));
  if (skill) {
    steps.value = (skill.steps || []).map((row) => ({
      kind: row.kind || "checkout",
      description: row.description || "",
    }));
  } else {
    steps.value = [{ kind: "checkout", description: "" }];
  }
  clearErrors();
}

function addInput() {
  if (inputs.value.length < 32) {
    inputs.value.push({ name: "", description: "", required: true });
  }
}

function addStep() {
  if (steps.value.length < 20) {
    steps.value.push({ kind: "checkout", description: "" });
  }
}

function clearErrors() {
  for (const key of Object.keys(errors)) {
    delete errors[key];
  }
}

function fieldMessage(key) {
  return errors[key] || props.serverErrors[key] || "";
}

function validate() {
  clearErrors();
  const nameMessage = skillNameError(form.name);
  if (nameMessage) errors.name = nameMessage;
  const versionMessage = versionError(form.version.trim());
  if (versionMessage) errors.version = versionMessage;
  const limits = [
    ["description", form.description, 2000, "说明过长"],
    ["toolchain", form.toolchain.trim(), 200, "工具链过长"],
    ["environment", form.environment.trim(), 200, "环境过长"],
    ["output_spec", form.output_spec.trim(), 500, "输出说明过长"],
  ];
  for (const [key, value, max, message] of limits) {
    const hit = boundError(value, max, message);
    if (hit) errors[key] = hit;
  }
  const repoMessage = repositoryMessage();
  if (repoMessage) errors.allowed_repositories = repoMessage;
  const dependencyMessage = scopeListError(form.allowed_dependencies);
  if (dependencyMessage) errors.allowed_dependencies = dependencyMessage;
  inputs.value.forEach((row, index) => {
    if (!row.name.trim() && !row.description.trim()) return;
    const inputMessage = inputNameError(row.name.trim());
    if (inputMessage) errors[`input-${index}-name`] = inputMessage;
    const inputDescription = boundError(row.description, 300, "说明过长");
    if (inputDescription) errors[`input-${index}-description`] = inputDescription;
  });
  steps.value.forEach((row, index) => {
    const kindMessage = stepKindError(row.kind);
    if (kindMessage) errors[`step-${index}-kind`] = kindMessage;
    const stepDescription = boundError(row.description, 500, "说明过长");
    if (stepDescription) errors[`step-${index}-description`] = stepDescription;
  });
  return Object.keys(errors).length === 0;
}

function submit() {
  if (props.busy || !validate()) return;
  emit("submit", {
    name: form.name.trim(),
    version: form.version.trim(),
    description: form.description.trim(),
    inputs: inputs.value
      .filter((row) => row.name.trim() || row.description.trim())
      .map((row) => ({
        name: row.name.trim(),
        description: row.description.trim(),
        required: Boolean(row.required),
      })),
    toolchain: form.toolchain.trim() || null,
    environment: form.environment.trim() || null,
    allowed_repositories: repositoryPayload(),
    allowed_dependencies: scopeList(form.allowed_dependencies),
    steps: steps.value.map((row) => ({ kind: row.kind, description: row.description.trim() })),
    output_spec: form.output_spec.trim() || null,
  });
}
</script>

<template>
  <form class="panel skill-form" @submit.prevent="submit">
    <PageIntro v-if="title" :title="title" :lead="lead" />
    <p v-else-if="lead" class="hint">{{ lead }}</p>
    <div v-for="field in scalars" :key="field[0]" class="field">
      <template v-if="field[0] === 'allowed_repositories'">
        <fieldset class="repo-field">
          <legend>允许仓库</legend>
          <p v-if="repoMode === 'checklist'" class="field-note">{{ REPO_UNRESTRICTED }}</p>
          <template v-else-if="repoMode === 'manual'">
            <p class="field-note">{{ REPO_MANUAL_UNRESTRICTED }}</p>
            <p v-if="repoNotice" class="field-note">{{ repoNotice }}</p>
          </template>
          <p v-else class="field-note">正在读取仓库…</p>
          <div v-if="repoMode === 'checklist'">
            <div v-if="repoChoices.length" class="repo-choices">
              <label v-for="row in repoChoices" :key="row.name" class="check">
                <input v-model="selectedRepos" type="checkbox" :value="row.name" />
                <span class="mono">{{ row.name }}</span>
                <span v-if="row.extra" class="repo-extra">不在当前列表，可以取消勾选</span>
              </label>
            </div>
            <label class="repo-manual" for="skill-repo-manual">手动添加</label>
            <input
              id="skill-repo-manual"
              v-model="repoManual"
              type="text"
              autocomplete="off"
              placeholder="owner/name"
            />
          </div>
          <textarea
            v-else-if="repoMode === 'manual'"
            id="skill-repos"
            v-model="form.allowed_repositories"
            rows="3"
            placeholder="一行一条，owner/name"
          />
        </fieldset>
      </template>
      <template v-else>
        <label :for="field[2]">{{ field[1] }}</label>
        <p v-if="field[5]" class="field-note">{{ field[5] }}</p>
        <textarea
          v-if="field[3]"
          :id="field[2]"
          v-model="form[field[0]]"
          :rows="field[3]"
          :placeholder="field[4]"
        />
        <input v-else :id="field[2]" v-model="form[field[0]]" type="text" autocomplete="off" />
      </template>
      <p v-if="fieldMessage(field[0])" class="field-error">{{ fieldMessage(field[0]) }}</p>
    </div>
    <div class="field">
      <label>输入</label>
      <div v-for="(row, index) in inputs" :key="index" class="repeat-row">
        <div>
          <input v-model="row.name" type="text" autocomplete="off" placeholder="名称" />
          <p v-if="fieldMessage(`input-${index}-name`)" class="field-error">
            {{ fieldMessage(`input-${index}-name`) }}
          </p>
        </div>
        <div>
          <input v-model="row.description" type="text" autocomplete="off" placeholder="说明" />
          <p v-if="fieldMessage(`input-${index}-description`)" class="field-error">
            {{ fieldMessage(`input-${index}-description`) }}
          </p>
        </div>
        <label class="check"><input v-model="row.required" type="checkbox" />必填</label>
        <button type="button" class="btn btn-ghost" @click="inputs.splice(index, 1)">删除</button>
      </div>
      <button v-if="inputs.length < 32" type="button" class="btn btn-secondary" @click="addInput">
        添加输入
      </button>
    </div>
    <div class="field">
      <label>步骤</label>
      <div v-for="(row, index) in steps" :key="index" class="repeat-row steps-row">
        <select v-model="row.kind">
          <option v-for="item in STEP_OPTIONS" :key="item[0]" :value="item[0]">{{ item[1] }}</option>
        </select>
        <input v-model="row.description" type="text" autocomplete="off" placeholder="说明" />
        <button type="button" class="btn btn-ghost" @click="steps.splice(index, 1)">删除</button>
        <p v-if="fieldMessage(`step-${index}-kind`) || fieldMessage(`step-${index}-description`)" class="field-error">
          {{ fieldMessage(`step-${index}-kind`) || fieldMessage(`step-${index}-description`) }}
        </p>
      </div>
      <button v-if="steps.length < 20" type="button" class="btn btn-secondary" @click="addStep">
        添加步骤
      </button>
    </div>
    <p v-if="formError" class="message error" role="alert">{{ formError }}</p>
    <div class="form-actions">
      <button type="submit" class="btn" :disabled="busy">
        <span v-if="busy" class="spinner" aria-hidden="true"></span>
        {{ submitLabel }}
      </button>
      <button v-if="showCancel" type="button" class="btn btn-secondary" :disabled="busy" @click="emit('cancel')">
        取消
      </button>
    </div>
  </form>
</template>

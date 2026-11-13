<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { accessReady, actionDeniedText, perms } from "../access";
import { errorText, isForbidden, postJson } from "../api";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import { pageCrumb } from "../crumb";
import {
  boundError,
  fieldErrors,
  inputNameError,
  scopeList,
  scopeListError,
  skillNameError,
  stepKindError,
  versionError,
} from "../forms";
import { canSee } from "../permissions";

const STEP_OPTIONS = [
  ["checkout", "检出"],
  ["compile", "编译"],
  ["package", "打包"],
  ["archive", "归档"],
];

const router = useRouter();
const forbidden = ref(false);
const busy = ref(false);
const formError = ref("");
const errors = reactive({});
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
  ["allowed_repositories", "允许仓库", "skill-repos", 3, "一行一条"],
  ["allowed_dependencies", "允许依赖", "skill-deps", 3, "一行一条"],
];

const blocked = computed(() => accessReady.value && !canSee("skill-new", perms.value));

onMounted(() => {
  pageCrumb.value = "Skill / 新建草稿";
});
watch(blocked, (value) => {
  forbidden.value = value;
}, { immediate: true });

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
  const repoMessage = scopeListError(form.allowed_repositories);
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

async function submit() {
  if (busy.value || !validate()) return;
  formError.value = "";
  const payload = {
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
    allowed_repositories: scopeList(form.allowed_repositories),
    allowed_dependencies: scopeList(form.allowed_dependencies),
    steps: steps.value.map((row) => ({ kind: row.kind, description: row.description.trim() })),
    output_spec: form.output_spec.trim() || null,
  };
  busy.value = true;
  try {
    const created = await postJson("/api/v1/skills", payload);
    await router.replace({ name: "skill", params: { id: created.id } });
  } catch (caught) {
    if (isForbidden(caught)) {
      formError.value = actionDeniedText("skill.use");
      return;
    }
    const mapped = fieldErrors(caught?.detail);
    if (caught?.status === 409 && caught.message) mapped.version = mapped.version || caught.message;
    if (Object.keys(mapped).length) {
      Object.assign(errors, mapped);
      return;
    }
    formError.value = errorText(caught, "无法创建 Skill");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="skill.use" />
  <form v-else class="panel skill-form" @submit.prevent="submit">
    <PageIntro title="新建草稿" lead="步骤只作为数据保存，不会执行。" />
    <div v-for="field in scalars" :key="field[0]" class="field">
      <label :for="field[2]">{{ field[1] }}</label>
      <textarea
        v-if="field[3]"
        :id="field[2]"
        v-model="form[field[0]]"
        :rows="field[3]"
        :placeholder="field[4]"
      />
      <input v-else :id="field[2]" v-model="form[field[0]]" type="text" autocomplete="off" />
      <p v-if="errors[field[0]]" class="field-error">{{ errors[field[0]] }}</p>
    </div>
    <div class="field">
      <label>输入</label>
      <div v-for="(row, index) in inputs" :key="index" class="repeat-row">
        <div>
          <input v-model="row.name" type="text" autocomplete="off" placeholder="名称" />
          <p v-if="errors[`input-${index}-name`]" class="field-error">{{ errors[`input-${index}-name`] }}</p>
        </div>
        <div>
          <input v-model="row.description" type="text" autocomplete="off" placeholder="说明" />
          <p v-if="errors[`input-${index}-description`]" class="field-error">{{ errors[`input-${index}-description`] }}</p>
        </div>
        <label class="check"><input v-model="row.required" type="checkbox" />必填</label>
        <button type="button" class="btn btn-ghost" @click="inputs.splice(index, 1)">删除</button>
      </div>
      <button v-if="inputs.length < 32" type="button" class="btn btn-secondary" @click="addInput">添加输入</button>
    </div>
    <div class="field">
      <label>步骤</label>
      <div v-for="(row, index) in steps" :key="index" class="repeat-row steps-row">
        <select v-model="row.kind">
          <option v-for="item in STEP_OPTIONS" :key="item[0]" :value="item[0]">{{ item[1] }}</option>
        </select>
        <input v-model="row.description" type="text" autocomplete="off" placeholder="说明" />
        <button type="button" class="btn btn-ghost" @click="steps.splice(index, 1)">删除</button>
        <p v-if="errors[`step-${index}-kind`] || errors[`step-${index}-description`]" class="field-error">
          {{ errors[`step-${index}-kind`] || errors[`step-${index}-description`] }}
        </p>
      </div>
      <button v-if="steps.length < 20" type="button" class="btn btn-secondary" @click="addStep">添加步骤</button>
    </div>
    <p v-if="formError" class="message error" role="alert">{{ formError }}</p>
    <button type="submit" class="btn" :disabled="busy">
      <span v-if="busy" class="spinner" aria-hidden="true"></span>
      提交
    </button>
  </form>
</template>

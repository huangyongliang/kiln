<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { accessReady, actionDeniedText, perms } from "../access";
import { errorText, isForbidden, postJson } from "../api";
import SkillDraftForm from "../components/SkillDraftForm.vue";
import StatePanel from "../components/StatePanel.vue";
import { pageCrumb } from "../crumb";
import { fieldErrors } from "../forms";
import { canSee } from "../permissions";

const router = useRouter();
const forbidden = ref(false);
const busy = ref(false);
const formError = ref("");
const serverErrors = reactive({});

const blocked = computed(() => accessReady.value && !canSee("skill-new", perms.value));

onMounted(() => {
  pageCrumb.value = "Skill / 新建草稿";
});
watch(blocked, (value) => {
  forbidden.value = value;
}, { immediate: true });

function clearServerErrors() {
  for (const key of Object.keys(serverErrors)) {
    delete serverErrors[key];
  }
}

async function create(payload) {
  if (busy.value) return;
  formError.value = "";
  clearServerErrors();
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
      Object.assign(serverErrors, mapped);
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
  <SkillDraftForm
    v-else
    title="新建草稿"
    lead="步骤只作为数据保存，不会执行。"
    submit-label="提交"
    :busy="busy"
    :form-error="formError"
    :server-errors="serverErrors"
    @submit="create"
  />
</template>

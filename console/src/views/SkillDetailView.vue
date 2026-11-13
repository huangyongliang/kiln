<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import { accessReady, account, actionDeniedText, perms } from "../access";
import { errorText, getJson, isForbidden, patchJson, postEmpty, postJson } from "../api";
import FormDialog from "../components/FormDialog.vue";
import PageIntro from "../components/PageIntro.vue";
import SkillDraftForm from "../components/SkillDraftForm.vue";
import StatePanel from "../components/StatePanel.vue";
import StatusTag from "../components/StatusTag.vue";
import { pageCrumb } from "../crumb";
import { codePointLength, fieldErrors, rejectReasonError } from "../forms";
import { formatTime, stepLabel } from "../format";
import { apiPath, isUuid } from "../paths";
import { canDo, canSee } from "../permissions";
import { canReviseSkill } from "../skillLists";

const route = useRoute();
const router = useRouter();
const skill = ref(null);
const mode = ref("loading");
const error = ref("");
const forbidden = ref(false);
const missing = ref(false);
const busy = ref("");
const actionError = ref("");
const rejectOpen = ref(false);
const rejectReason = ref("");
const rejectError = ref("");
const rejectField = ref("");
const rejectorName = ref("");
const editing = ref(false);
const editError = ref("");
const editErrors = reactive({});

const canEdit = computed(() =>
  canReviseSkill(skill.value, perms.value, account.value?.id || ""),
);
const canSubmit = computed(
  () => skill.value?.status === "draft" && canDo("skill.submit", perms.value),
);
const canPublish = computed(
  () => skill.value?.status === "in_review" && canDo("skill.publish", perms.value),
);
const canReject = computed(
  () => skill.value?.status === "in_review" && canDo("skill.reject", perms.value),
);
const canStartBuild = computed(
  () =>
    skill.value?.status === "published" &&
    canDo("executor.run", perms.value) &&
    canDo("skill.create", perms.value),
);
const reviewTitle = computed(() => {
  if (!skill.value?.review_note || skill.value.status === "published") {
    return "";
  }
  if (skill.value.status === "draft") {
    return "退回原因";
  }
  if (skill.value.status === "in_review") {
    return "上次退回原因";
  }
  return "";
});
const reasonCount = computed(() => codePointLength(rejectReason.value.trim()));

onMounted(load);
watch(
  () => route.params.id,
  () => load(),
);
watch(accessReady, (ready) => {
  if (ready) {
    load();
  }
});
watch(skill, (value) => {
  loadRejector(value);
});

function clearEditErrors() {
  for (const key of Object.keys(editErrors)) {
    delete editErrors[key];
  }
}

function cancelEdit() {
  editing.value = false;
  editError.value = "";
  clearEditErrors();
  if (route.query.edit) {
    router.replace({ name: "skill", params: { id: route.params.id } });
  }
}

async function saveEdit(payload) {
  const id = route.params.id;
  if (busy.value || !isUuid(id)) return;
  editError.value = "";
  clearEditErrors();
  busy.value = "edit";
  try {
    const updated = await patchJson(apiPath`/api/v1/skills/${id}`, payload);
    if (route.query.edit) {
      await router.replace({ name: "skill", params: { id: updated.id } });
    }
    skill.value = updated;
    pageCrumb.value = `Skill / ${skill.value.name}`;
    editing.value = false;
  } catch (caught) {
    if (isForbidden(caught)) {
      editError.value = actionDeniedText("skill.use");
      return;
    }
    if (caught?.status === 409 && String(caught.message || "").includes("同名同版本")) {
      editErrors.version = caught.message;
      return;
    }
    const mapped = fieldErrors(caught?.detail);
    if (Object.keys(mapped).length) {
      Object.assign(editErrors, mapped);
      return;
    }
    editError.value = errorText(caught, "无法保存草稿");
  } finally {
    busy.value = "";
  }
}

async function load() {
  editing.value = false;
  forbidden.value = false;
  actionError.value = "";
  editError.value = "";
  missing.value = false;
  const id = route.params.id;
  if (!isUuid(id)) {
    skill.value = null;
    missing.value = true;
    mode.value = "ready";
    pageCrumb.value = "Skill";
    return;
  }
  if (accessReady.value && !canSee("skill", perms.value)) {
    forbidden.value = true;
    return;
  }
  mode.value = "loading";
  error.value = "";
  try {
    skill.value = await getJson(apiPath`/api/v1/skills/${id}`);
    pageCrumb.value = `Skill / ${skill.value.name}`;
    editing.value = route.query.edit === "1" && canEdit.value;
    mode.value = "ready";
  } catch (caught) {
    if (isForbidden(caught)) {
      forbidden.value = true;
      return;
    }
    mode.value = "error";
    error.value = errorText(caught, "无法读取 Skill");
  }
}

async function act(kind) {
  const id = route.params.id;
  if (busy.value || !isUuid(id)) {
    return;
  }
  busy.value = kind;
  actionError.value = "";
  const path =
    kind === "submit"
      ? apiPath`/api/v1/skills/${id}/submit-review`
      : apiPath`/api/v1/skills/${id}/publish`;
  try {
    skill.value = await postEmpty(path);
    pageCrumb.value = `Skill / ${skill.value.name}`;
  } catch (caught) {
    if (isForbidden(caught)) {
      actionError.value = actionDeniedText(kind === "submit" ? "skill.submit" : "skill.publish");
    } else {
      actionError.value = errorText(caught, kind === "submit" ? "无法提交审核" : "无法发布");
    }
  } finally {
    busy.value = "";
  }
}

async function loadRejector(value) {
  rejectorName.value = "";
  if (!value?.review_note || value.status === "published") {
    return;
  }
  if (!value.rejected_by) {
    rejectorName.value = value.rejected_at ? "机器令牌" : "—";
    return;
  }
  if (!canDo("account.read", perms.value)) {
    rejectorName.value = "—";
    return;
  }
  try {
    const person = await getJson(apiPath`/api/v1/accounts/${value.rejected_by}`);
    rejectorName.value = person.display_name || person.username || "—";
  } catch {
    rejectorName.value = "—";
  }
}

function openReject() {
  rejectReason.value = "";
  rejectError.value = "";
  rejectField.value = "";
  rejectOpen.value = true;
}

function closeReject() {
  rejectOpen.value = false;
}

async function submitReject() {
  const id = route.params.id;
  const message = rejectReasonError(rejectReason.value);
  rejectField.value = message;
  rejectError.value = "";
  if (message || busy.value || !isUuid(id)) {
    return;
  }
  busy.value = "reject";
  try {
    skill.value = await postJson(apiPath`/api/v1/skills/${id}/reject`, {
      reason: rejectReason.value.trim(),
    });
    pageCrumb.value = `Skill / ${skill.value.name}`;
    rejectOpen.value = false;
  } catch (caught) {
    if (isForbidden(caught)) {
      rejectError.value = actionDeniedText("skill.publish");
      return;
    }
    const mapped = fieldErrors(caught?.detail);
    if (mapped.reason) {
      rejectField.value = mapped.reason;
      return;
    }
    rejectError.value = errorText(caught, "无法退回草稿");
  } finally {
    busy.value = "";
  }
}
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="skill.read" />
  <template v-else>
    <PageIntro :title="skill?.name || 'Skill'">
      <template v-if="skill" #meta>
        <p class="meta-row">
          <StatusTag :status="skill.status" :label="skill.status_label" />
          <span v-if="skill.status === 'draft' && skill.review_note" class="tag">已退回</span>
          <span class="mono">{{ skill.version }}</span>
        </p>
      </template>
      <template v-if="!editing && (canEdit || canSubmit || canPublish || canReject || canStartBuild)" #actions>
        <button v-if="canEdit" type="button" class="btn btn-secondary" :disabled="Boolean(busy)" @click="editing = true">
          编辑
        </button>
        <button v-if="canSubmit" type="button" class="btn" :disabled="Boolean(busy)" @click="act('submit')">
          <span v-if="busy === 'submit'" class="spinner" aria-hidden="true"></span>
          提交审核
        </button>
        <button v-if="canReject" type="button" class="btn btn-secondary" :disabled="Boolean(busy)" @click="openReject">
          退回草稿
        </button>
        <button v-if="canPublish" type="button" class="btn" :disabled="Boolean(busy)" @click="act('publish')">
          <span v-if="busy === 'publish'" class="spinner" aria-hidden="true"></span>
          发布
        </button>
        <RouterLink
          v-if="canStartBuild"
          class="btn"
          :to="{ name: 'builds', query: { skill: skill.id } }"
        >
          用此流程构建
        </RouterLink>
      </template>
    </PageIntro>
    <p v-if="skill?.description && !editing" class="prose">{{ skill.description }}</p>
    <section v-if="skill && reviewTitle" class="panel">
      <div class="panel-head">
        <h2>{{ reviewTitle }}</h2>
      </div>
      <p class="review-note">{{ skill.review_note }}</p>
      <p class="hint">{{ formatTime(skill.rejected_at) }} · {{ rejectorName || "—" }}</p>
    </section>
    <p v-if="actionError" class="message error" role="alert">{{ actionError }}</p>
    <SkillDraftForm
      v-if="editing && skill && mode === 'ready'"
      :initial="skill"
      lead="步骤只作为数据保存，不会执行。"
      submit-label="保存"
      show-cancel
      :busy="busy === 'edit'"
      :form-error="editError"
      :server-errors="editErrors"
      @submit="saveEdit"
      @cancel="cancelEdit"
    />
    <StatePanel
      v-if="!editing"
      :loading="mode === 'loading'"
      :error="mode === 'error' ? error : ''"
      :empty="missing"
      empty-text="不存在"
      @retry="load"
    >
      <div v-if="skill" class="grid-2 even">
        <section class="panel">
          <div class="panel-head">
            <h2>步骤</h2>
          </div>
          <p v-if="skill.steps.length === 0" class="message">还没有步骤。</p>
          <ol v-else class="steps">
            <li v-for="(step, index) in skill.steps" :key="index">
              <strong>{{ index + 1 }}. {{ stepLabel(step.kind) }}</strong>
              <div v-if="step.description">{{ step.description }}</div>
            </li>
          </ol>
          <p class="hint">步骤只作为数据保存，不会执行</p>
        </section>
        <section class="panel">
          <div class="panel-head">
            <h2>范围</h2>
          </div>
          <dl class="kv">
            <dt>工具链</dt>
            <dd>{{ skill.toolchain || "—" }}</dd>
            <dt>环境</dt>
            <dd>{{ skill.environment || "—" }}</dd>
            <dt>输入</dt>
            <dd>
              <template v-if="skill.inputs.length">
                <div v-for="input in skill.inputs" :key="input.name">
                  {{ input.name }}<span v-if="input.required">（必填）</span>
                  <span v-if="input.description" class="muted"> · {{ input.description }}</span>
                </div>
              </template>
              <template v-else>—</template>
            </dd>
            <dt>仓库</dt>
            <dd>{{ skill.allowed_repositories.length ? skill.allowed_repositories.join("、") : "—" }}</dd>
          </dl>
        </section>
      </div>
    </StatePanel>
    <FormDialog
      v-if="rejectOpen"
      title="退回草稿"
      :busy="busy === 'reject'"
      :error="rejectError"
      @close="closeReject"
      @submit="submitReject"
    >
      <div class="field">
        <label for="reject-reason">原因</label>
        <textarea id="reject-reason" v-model="rejectReason" rows="5"></textarea>
        <p class="counter">{{ reasonCount }}/500</p>
        <p v-if="rejectField" class="field-error">{{ rejectField }}</p>
      </div>
    </FormDialog>
  </template>
</template>

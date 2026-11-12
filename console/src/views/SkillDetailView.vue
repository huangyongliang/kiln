<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import { accessReady, actionDeniedText, perms } from "../access";
import { errorText, getJson, isForbidden, postEmpty } from "../api";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import StatusTag from "../components/StatusTag.vue";
import { pageCrumb } from "../crumb";
import { stepLabel } from "../format";
import { apiPath, isUuid } from "../paths";
import { canDo, canSee } from "../permissions";

const route = useRoute();
const skill = ref(null);
const mode = ref("loading");
const error = ref("");
const forbidden = ref(false);
const missing = ref(false);
const busy = ref("");
const actionError = ref("");

const canSubmit = computed(
  () => skill.value?.status === "draft" && canDo("skill.submit", perms.value),
);
const canPublish = computed(
  () => skill.value?.status === "in_review" && canDo("skill.publish", perms.value),
);

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

async function load() {
  forbidden.value = false;
  actionError.value = "";
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
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="skill.read" />
  <template v-else>
    <PageIntro :title="skill?.name || 'Skill'">
      <template v-if="skill" #meta>
        <p class="meta-row">
          <StatusTag :status="skill.status" :label="skill.status_label" />
          <span class="mono">{{ skill.version }}</span>
        </p>
      </template>
      <template v-if="canSubmit || canPublish" #actions>
        <button v-if="canSubmit" type="button" class="btn" :disabled="Boolean(busy)" @click="act('submit')">
          <span v-if="busy === 'submit'" class="spinner" aria-hidden="true"></span>
          提交审核
        </button>
        <button v-if="canPublish" type="button" class="btn" :disabled="Boolean(busy)" @click="act('publish')">
          <span v-if="busy === 'publish'" class="spinner" aria-hidden="true"></span>
          发布
        </button>
      </template>
    </PageIntro>
    <p v-if="skill?.description" class="prose">{{ skill.description }}</p>
    <p v-if="actionError" class="message error" role="alert">{{ actionError }}</p>
    <StatePanel
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
  </template>
</template>

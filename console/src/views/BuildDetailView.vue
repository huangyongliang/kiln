<script setup>
import { onMounted, onUnmounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import { accessReady, perms } from "../access";
import { errorText, getJson, isForbidden, isNotConfigured } from "../api";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import StatusTag from "../components/StatusTag.vue";
import { pageCrumb } from "../crumb";
import { formatTime, packageLabel, shortSha } from "../format";
import { apiPath, isUuid } from "../paths";
import { canSee } from "../permissions";

const TERMINAL = new Set(["success", "failure", "unstable", "aborted", "not_built"]);

const route = useRoute();
const build = ref(null);
const logText = ref("");
const truncated = ref(false);
const mode = ref("loading");
const error = ref("");
const forbidden = ref(false);
const missing = ref(false);
let timer = 0;

onMounted(load);
onUnmounted(stop);
watch(
  () => route.params.id,
  () => load(),
);
watch(accessReady, (ready) => {
  if (ready) {
    load();
  }
});

function stop() {
  window.clearInterval(timer);
  timer = 0;
}

async function load() {
  stop();
  forbidden.value = false;
  error.value = "";
  missing.value = false;
  if (!isUuid(route.params.id)) {
    build.value = null;
    missing.value = true;
    mode.value = "ready";
    pageCrumb.value = "构建";
    return;
  }
  if (accessReady.value && !canSee("build", perms.value)) {
    forbidden.value = true;
    mode.value = "ready";
    return;
  }
  mode.value = "loading";
  const ok = await refresh();
  if (ok && build.value && !TERMINAL.has(build.value.status)) {
    timer = window.setInterval(refresh, 2000);
  }
}

async function refresh() {
  const id = route.params.id;
  if (!isUuid(id)) {
    return false;
  }
  try {
    const next = await getJson(apiPath`/api/v1/builds/${id}`);
    build.value = next;
    pageCrumb.value = `构建 / ${next.build_number ? `#${next.build_number}` : shortSha(next.commit_sha)}`;
    mode.value = "ready";
    if (next.build_number) {
      try {
        const log = await getJson(apiPath`/api/v1/builds/${next.id}/log`);
        logText.value = log.text || "";
        truncated.value = Boolean(log.truncated);
      } catch (caught) {
        if (!isForbidden(caught)) {
          logText.value = "";
        }
      }
    } else {
      logText.value = "";
      truncated.value = false;
    }
    if (TERMINAL.has(next.status)) {
      stop();
    }
    return true;
  } catch (caught) {
    if (isForbidden(caught)) {
      forbidden.value = true;
      stop();
      return false;
    }
    if (isNotConfigured(caught)) {
      mode.value = "unconfigured";
      stop();
      return false;
    }
    error.value = errorText(caught, "无法读取构建");
    mode.value = "error";
    stop();
    return false;
  }
}
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="executor.read" />
  <template v-else>
    <PageIntro :title="build?.repository || '构建'" lead="一次产品构建的状态、提交和日志。">
      <template v-if="build" #meta>
        <p class="meta-row">
          <StatusTag :status="build.status" />
          <span class="mono">{{ shortSha(build.commit_sha) }}</span>
        </p>
      </template>
    </PageIntro>
    <StatePanel
      :loading="mode === 'loading'"
      :error="mode === 'error' ? error : ''"
      :empty="missing || mode === 'unconfigured'"
      :empty-text="missing ? '不存在' : '执行器未配置'"
      @retry="load"
    >
      <section v-if="build" class="panel">
        <dl class="kv">
          <dt>编号</dt>
          <dd class="mono">{{ build.build_number ?? "—" }}</dd>
          <dt>仓库</dt>
          <dd>{{ build.repository }}</dd>
          <dt>引用</dt>
          <dd>{{ build.ref }}</dd>
          <dt>提交</dt>
          <dd class="mono">{{ build.commit_sha || "—" }}</dd>
          <dt>版本</dt>
          <dd class="mono">{{ build.version }}</dd>
          <dt>类型</dt>
          <dd>{{ packageLabel(build.package_type) }}</dd>
          <dt>制品</dt>
          <dd>
            <RouterLink class="linkish" :to="{ name: 'artifact', params: { id: build.artifact_id } }">查看制品</RouterLink>
          </dd>
          <dt>创建</dt>
          <dd>{{ formatTime(build.created_at) }}</dd>
          <dt>更新</dt>
          <dd>{{ formatTime(build.updated_at) }}</dd>
        </dl>
      </section>
      <section v-if="build" class="panel">
        <div class="panel-head">
          <h2>日志</h2>
        </div>
        <p v-if="!build.build_number" class="message">日志尚未生成。</p>
        <pre v-else class="log">{{ logText || "日志还是空的。" }}</pre>
        <p v-if="truncated" class="hint">日志已截断。</p>
      </section>
    </StatePanel>
  </template>
</template>

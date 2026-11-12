<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { RouterLink, useRouter } from "vue-router";

import { accessReady, perms } from "../access";
import { errorText, getJson, isForbidden, isNotConfigured, postJson } from "../api";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import StatusTag from "../components/StatusTag.vue";
import { formatTime, shortSha } from "../format";
import { canDo, canSee } from "../permissions";

const note =
  "兼容执行器在已配置的 Jenkins 上创建任务，并取回状态和日志。产品构建从代码仓库检出提交，在 Jenkins 上运行仓库里的 kiln-build.sh，把产出的字节存进平台目录。代码源按适配器读取：Gitea 是实验室实现，GitLab 是生产代码源。平台不把制品部署到运行环境。";

const roleText = {
  lab: "实验室",
  production: "生产代码源",
  unconfigured: "未配置",
};

const TERMINAL = ["success", "failure", "unstable", "aborted", "not_built"];

const router = useRouter();
const tab = ref("product");
const executor = ref(null);
const source = ref(null);
const builds = ref([]);
const jobs = ref([]);
const repositories = ref([]);
const pageError = ref("");
const forbidden = ref(false);
const buildsMode = ref("loading");
const buildsError = ref("");
const jobsError = ref("");
const sourceError = ref("");
const query = ref("");
const statusFilter = ref("all");
const name = ref("");
const marker = ref("kiln-ok");
const active = ref(null);
const logText = ref("");
const busy = ref(false);
const actionError = ref("");
let timer = 0;

const showSource = computed(() => perms.value == null || canDo("git.read", perms.value));
const showJobForm = computed(
  () => Boolean(executor.value?.configured) && canDo("job.create", perms.value),
);

const filteredBuilds = computed(() => {
  const needle = query.value.trim().toLowerCase();
  return builds.value.filter((item) => {
    if (!matchesStatus(item.status, statusFilter.value)) {
      return false;
    }
    if (!needle) {
      return true;
    }
    return [item.repository, item.ref, item.commit_sha, item.version, item.build_number, item.name]
      .join(" ")
      .toLowerCase()
      .includes(needle);
  });
});

onMounted(load);
onUnmounted(stopPoll);
watch(accessReady, (ready) => {
  if (ready) {
    load();
  }
});

function matchesStatus(status, filter) {
  if (filter === "all") {
    return true;
  }
  if (filter === "running") {
    return status === "queued" || status === "running";
  }
  if (filter === "failure") {
    return status === "failure" || status === "aborted";
  }
  return status === filter;
}

function stopPoll() {
  window.clearInterval(timer);
  timer = 0;
}

async function load() {
  forbidden.value = false;
  pageError.value = "";
  if (accessReady.value && !canSee("builds", perms.value)) {
    forbidden.value = true;
    return;
  }
  try {
    executor.value = await getJson("/api/v1/executor");
  } catch (caught) {
    if (isForbidden(caught)) {
      forbidden.value = true;
      return;
    }
    pageError.value = errorText(caught, "无法读取执行器");
    return;
  }
  await Promise.all([loadBuilds(), loadJobs(), loadSource()]);
}

async function loadBuilds() {
  buildsMode.value = "loading";
  buildsError.value = "";
  try {
    const body = await getJson("/api/v1/builds");
    builds.value = body.items || [];
    buildsMode.value = "ready";
  } catch (caught) {
    if (isForbidden(caught)) {
      forbidden.value = true;
      return;
    }
    if (isNotConfigured(caught)) {
      builds.value = [];
      buildsMode.value = "unconfigured";
      return;
    }
    buildsMode.value = "error";
    buildsError.value = errorText(caught, "无法读取构建");
  }
}

async function loadJobs() {
  jobsError.value = "";
  jobs.value = [];
  if (executor.value && !executor.value.configured) {
    return;
  }
  try {
    const body = await getJson("/api/v1/jobs");
    jobs.value = body.items || [];
  } catch (caught) {
    if (isForbidden(caught)) {
      forbidden.value = true;
      return;
    }
    if (isNotConfigured(caught)) {
      return;
    }
    jobsError.value = errorText(caught, "无法读取任务");
  }
}

async function loadSource() {
  source.value = null;
  sourceError.value = "";
  repositories.value = [];
  if (!showSource.value) {
    return;
  }
  try {
    source.value = await getJson("/api/v1/git/source");
  } catch (caught) {
    if (isForbidden(caught)) {
      sourceError.value = errorText(caught, "没有权限");
      return;
    }
    sourceError.value = errorText(caught, "无法读取代码源");
    return;
  }
  if (!source.value?.configured) {
    return;
  }
  try {
    const body = await getJson("/api/v1/git/repositories");
    repositories.value = body.items || [];
  } catch (caught) {
    if (isNotConfigured(caught)) {
      return;
    }
    sourceError.value = errorText(caught, "无法读取仓库");
  }
}

async function createJob() {
  const value = marker.value.trim();
  if (!value || busy.value) {
    return;
  }
  busy.value = true;
  actionError.value = "";
  logText.value = "";
  try {
    const payload = { marker: value };
    if (name.value.trim()) {
      payload.name = name.value.trim();
    }
    active.value = await postJson("/api/v1/jobs", payload);
    stopPoll();
    timer = window.setInterval(refreshActive, 2000);
    await refreshActive();
    await loadJobs();
  } catch (caught) {
    actionError.value = errorText(caught, "无法创建任务");
  } finally {
    busy.value = false;
  }
}

async function refreshActive() {
  if (!active.value) {
    return;
  }
  try {
    active.value = await getJson(`/api/v1/jobs/${active.value.id}`);
    if (active.value.build_number) {
      const log = await getJson(`/api/v1/jobs/${active.value.id}/log`);
      logText.value = log.text;
    }
    if (TERMINAL.includes(active.value.status)) {
      stopPoll();
    }
  } catch (caught) {
    actionError.value = errorText(caught, "无法刷新任务");
    stopPoll();
  }
}

function openBuild(id) {
  router.push(`/builds/${id}`);
}
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="executor.read" />
  <template v-else>
    <PageIntro title="构建" lead="查看产品构建和兼容任务的状态与日志。" :note="note" />
    <StatePanel :error="pageError" @retry="load">
      <section v-if="executor" class="env-strip">
        <span>类型 {{ executor.kind === "jenkins" ? "Jenkins" : "未配置" }}</span>
        <span>地址 {{ executor.base_url || "—" }}</span>
        <template v-if="showSource && source">
          <span>代码源 {{ source.provider === "off" ? "未配置" : source.provider }}</span>
          <span>角色 {{ roleText[source.role] || source.role }}</span>
        </template>
      </section>
      <p v-if="showSource && source" class="hint env-hint">
        生产代码源是 GitLab。地址只来自 GIT_BASE_URL，仓库里不写生产地址。
      </p>
      <p v-if="showSource && sourceError" class="message error">{{ sourceError }}</p>

      <div class="tabs" role="tablist">
        <button type="button" class="chip" :class="{ active: tab === 'product' }" @click="tab = 'product'">
          产品构建
        </button>
        <button type="button" class="chip" :class="{ active: tab === 'jobs' }" @click="tab = 'jobs'">
          兼容任务
        </button>
      </div>

      <section v-if="tab === 'product'" class="panel">
        <div class="toolbar">
          <input v-model="query" class="search" type="search" placeholder="搜索仓库、引用或版本" />
          <button
            v-for="item in [
              ['all', '全部'],
              ['running', '进行中'],
              ['success', '成功'],
              ['failure', '失败'],
              ['unstable', '不稳定'],
            ]"
            :key="item[0]"
            type="button"
            class="chip"
            :class="{ active: statusFilter === item[0] }"
            @click="statusFilter = item[0]"
          >
            {{ item[1] }}
          </button>
        </div>
        <StatePanel
          :loading="buildsMode === 'loading'"
          :error="buildsMode === 'error' ? buildsError : ''"
          :empty="buildsMode === 'unconfigured' || (buildsMode === 'ready' && filteredBuilds.length === 0)"
          :empty-text="
            buildsMode === 'unconfigured'
              ? '执行器未配置'
              : builds.length
                ? '没有符合条件的构建'
                : '还没有构建'
          "
          @retry="loadBuilds"
        >
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th class="nowrap">编号</th>
                  <th>仓库</th>
                  <th>引用</th>
                  <th class="nowrap">状态</th>
                  <th class="nowrap">版本</th>
                  <th class="nowrap">创建</th>
                  <th class="nowrap">更新</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in filteredBuilds"
                  :key="item.id"
                  class="clickable"
                  @click="openBuild(item.id)"
                >
                  <td class="mono nowrap">
                    <RouterLink class="linkish" :to="`/builds/${item.id}`" @click.stop>
                      {{ item.build_number ?? "—" }}
                    </RouterLink>
                  </td>
                  <td>{{ item.repository }}</td>
                  <td>
                    <div>{{ item.ref }}</div>
                    <div class="mono subline">{{ shortSha(item.commit_sha) }}</div>
                  </td>
                  <td class="nowrap"><StatusTag :status="item.status" /></td>
                  <td class="mono nowrap">{{ item.version }}</td>
                  <td class="nowrap">{{ formatTime(item.created_at) }}</td>
                  <td class="nowrap">{{ formatTime(item.updated_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </StatePanel>
      </section>

      <section v-else class="panel">
        <StatePanel
          v-if="executor && !executor.configured"
          empty
          empty-text="执行器未配置"
        />
        <template v-else>
          <form v-if="showJobForm" class="job-form" @submit.prevent="createJob">
            <h2>新建任务</h2>
            <div class="field">
              <label for="job-name">名称</label>
              <input id="job-name" v-model="name" type="text" autocomplete="off" />
            </div>
            <div class="field">
              <label for="job-marker">标记</label>
              <input id="job-marker" v-model="marker" type="text" autocomplete="off" required />
            </div>
            <button type="submit" class="btn" :disabled="busy">
              <span v-if="busy" class="spinner" aria-hidden="true"></span>
              创建任务
            </button>
            <p v-if="actionError" class="message error" role="alert">{{ actionError }}</p>
            <div v-if="active" class="job-status">
              <p class="message">
                {{ active.name || "任务" }}
                <StatusTag :status="active.status" />
                · 构建 {{ active.build_number || "尚未分配" }}
              </p>
              <pre v-if="logText" class="log">{{ logText }}</pre>
            </div>
          </form>
          <StatePanel :error="jobsError" :empty="!jobsError && jobs.length === 0" empty-text="还没有任务" @retry="loadJobs">
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>状态</th>
                    <th>构建号</th>
                    <th>标记</th>
                    <th>更新</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in jobs" :key="item.id">
                    <td>{{ item.name || "—" }}</td>
                    <td class="nowrap"><StatusTag :status="item.status" /></td>
                    <td class="mono nowrap">{{ item.build_number || "—" }}</td>
                    <td class="mono">{{ item.marker }}</td>
                    <td class="nowrap">{{ formatTime(item.updated_at) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </StatePanel>
        </template>
      </section>

      <section v-if="showSource && repositories.length" class="panel">
        <div class="panel-head">
          <h2>仓库</h2>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>仓库</th>
                <th>默认分支</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in repositories" :key="item.full_name">
                <td class="mono">{{ item.full_name }}</td>
                <td>{{ item.default_branch || "—" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </StatePanel>
  </template>
</template>

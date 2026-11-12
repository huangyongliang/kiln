<script setup>
import { onMounted, onUnmounted, ref } from "vue";

import { getJson, postJson } from "../api";
import PageIntro from "../components/PageIntro.vue";

const executor = ref(null);
const source = ref(null);
const jobs = ref([]);
const repositories = ref([]);
const name = ref("");
const marker = ref("kiln-ok");
const active = ref(null);
const logText = ref("");
const error = ref("");
const busy = ref(false);
let timer = 0;

const roleText = {
  lab: "实验室",
  production: "生产代码源",
  unconfigured: "未配置",
};

onMounted(load);
onUnmounted(() => window.clearInterval(timer));

async function load() {
  error.value = "";
  try {
    executor.value = await getJson("/api/v1/executor");
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "无法读取执行器";
  }
  try {
    source.value = await getJson("/api/v1/git/source");
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "无法读取代码源";
  }
  if (executor.value?.configured) {
    try {
      const body = await getJson("/api/v1/jobs");
      jobs.value = body.items;
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : "无法读取任务";
    }
  }
  if (source.value?.configured) {
    try {
      const body = await getJson("/api/v1/git/repositories");
      repositories.value = body.items;
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : "无法读取仓库";
    }
  }
}

async function createJob() {
  const value = marker.value.trim();
  if (!value || busy.value) {
    return;
  }
  busy.value = true;
  error.value = "";
  logText.value = "";
  try {
    const payload = { marker: value };
    if (name.value.trim()) {
      payload.name = name.value.trim();
    }
    active.value = await postJson("/api/v1/jobs", payload);
    window.clearInterval(timer);
    timer = window.setInterval(refreshActive, 2000);
    await refreshActive();
    await load();
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "无法创建任务";
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
    if (["success", "failure", "unstable", "aborted", "not_built"].includes(active.value.status)) {
      window.clearInterval(timer);
      timer = 0;
    }
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "无法刷新任务";
    window.clearInterval(timer);
    timer = 0;
  }
}
</script>

<template>
  <PageIntro
    title="构建"
    lead="兼容执行器在已配置的 Jenkins 上创建任务，并取回状态和日志。代码源按适配器读取：Gitea 是实验室实现，GitLab 是生产代码源。平台不把制品部署到运行环境。"
  />
  <p v-if="error" class="message error">{{ error }}</p>
  <section class="panel">
    <h2>执行器</h2>
    <dl v-if="executor">
      <dt>类型</dt>
      <dd>{{ executor.kind === "jenkins" ? "Jenkins" : "未配置" }}</dd>
      <dt>已配置</dt>
      <dd>{{ executor.configured ? "是" : "否" }}</dd>
      <dt>地址</dt>
      <dd>{{ executor.base_url || "—" }}</dd>
      <dt>部署到运行环境</dt>
      <dd>否</dd>
    </dl>
    <p v-else class="message">正在读取执行器 …</p>
  </section>
  <section v-if="executor?.configured" class="panel">
    <h2>新建任务</h2>
    <form @submit.prevent="createJob">
      <label for="job-name">名称</label>
      <input id="job-name" v-model="name" type="text" autocomplete="off" />
      <label for="job-marker">标记</label>
      <input id="job-marker" v-model="marker" type="text" autocomplete="off" required />
      <button type="submit" :disabled="busy">创建任务</button>
    </form>
    <div v-if="active" class="job-status">
      <p class="message">{{ active.name }} · {{ active.status }} · 构建 {{ active.build_number || "尚未分配" }}</p>
      <pre v-if="logText" class="reply">{{ logText }}</pre>
    </div>
  </section>
  <section class="panel">
    <h2>代码源</h2>
    <dl v-if="source">
      <dt>适配器</dt>
      <dd>{{ source.provider }}</dd>
      <dt>角色</dt>
      <dd>{{ roleText[source.role] || source.role }}</dd>
      <dt>生产适配器</dt>
      <dd>{{ source.production_provider }}</dd>
      <dt>地址</dt>
      <dd>{{ source.base_url || "—" }}</dd>
    </dl>
    <p v-else class="message">正在读取代码源 …</p>
    <p class="message">生产代码源是 GitLab。地址只来自 GIT_BASE_URL，仓库里不写生产地址。</p>
  </section>
  <section v-if="jobs.length" class="panel">
    <h2>任务</h2>
    <table>
      <thead>
        <tr>
          <th>名称</th>
          <th>状态</th>
          <th>构建号</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in jobs" :key="item.id">
          <td>{{ item.name }}</td>
          <td>{{ item.status }}</td>
          <td>{{ item.build_number || "—" }}</td>
        </tr>
      </tbody>
    </table>
  </section>
  <section v-if="repositories.length" class="panel">
    <h2>仓库</h2>
    <table>
      <thead>
        <tr>
          <th>仓库</th>
          <th>默认分支</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in repositories" :key="item.full_name">
          <td>{{ item.full_name }}</td>
          <td>{{ item.default_branch || "—" }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

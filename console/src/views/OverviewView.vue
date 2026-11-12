<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";

import { accessReady, perms } from "../access";
import { errorText, getJson, isForbidden, isNotConfigured } from "../api";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import StatusTag from "../components/StatusTag.vue";
import { formatTime, latestVersion } from "../format";
import { canDo } from "../permissions";

const note =
  "这是 kiln 的一期控制台。页面用于确认后端可访问，并进入构建、制品、skill、账号和对话。构建任务交给已配置的 Jenkins，平台不把制品部署到运行环境。";

const builds = ref([]);
const skills = ref([]);
const artifacts = ref([]);
const buildsState = ref("idle");
const skillsState = ref("idle");
const artifactsState = ref("idle");
const buildsError = ref("");
const skillsError = ref("");
const artifactsError = ref("");

const showBuilds = computed(() => canDo("overview.builds", perms.value));
const showSkills = computed(() => canDo("overview.skills", perms.value));
const showArtifacts = computed(() => canDo("overview.artifacts", perms.value));
const topCount = computed(() => Number(showBuilds.value) + Number(showSkills.value));
const metricCount = computed(() => {
  let count = 0;
  if (showBuilds.value && buildsState.value !== "hidden") {
    count += 1;
  }
  if (showSkills.value && skillsState.value !== "hidden") {
    count += 1;
  }
  if (showArtifacts.value && artifactsState.value !== "hidden") {
    count += 1;
  }
  return count;
});

const runningCount = computed(
  () => builds.value.filter((item) => item.status === "queued" || item.status === "running").length,
);
const skillMetric = computed(() => {
  if (perms.value == null || canDo("skill.publish", perms.value)) {
    return {
      label: "审核中",
      value: skills.value.filter((item) => item.status === "in_review").length,
    };
  }
  if (canDo("skill.submit", perms.value)) {
    return {
      label: "草稿",
      value: skills.value.filter((item) => item.status === "draft").length,
    };
  }
  return {
    label: "已发布 Skill",
    value: skills.value.filter((item) => item.status === "published").length,
  };
});
const reviewSkills = computed(() =>
  skills.value.filter((item) => item.status === "draft" || item.status === "in_review").slice(0, 5),
);

onMounted(load);
watch(accessReady, (ready) => {
  if (ready) {
    load();
  }
});

function load() {
  if (showBuilds.value) {
    loadBuilds();
  }
  if (showSkills.value) {
    loadSkills();
  }
  if (showArtifacts.value) {
    loadArtifacts();
  }
}

async function loadBuilds() {
  buildsState.value = "loading";
  buildsError.value = "";
  try {
    const body = await getJson("/api/v1/builds");
    builds.value = body.items || [];
    buildsState.value = builds.value.length ? "ready" : "empty";
  } catch (caught) {
    if (isForbidden(caught)) {
      buildsState.value = "hidden";
      return;
    }
    if (isNotConfigured(caught)) {
      buildsState.value = "unconfigured";
      builds.value = [];
      return;
    }
    buildsState.value = "error";
    buildsError.value = errorText(caught, "无法读取构建");
  }
}

async function loadSkills() {
  skillsState.value = "loading";
  skillsError.value = "";
  try {
    const body = await getJson("/api/v1/skills");
    skills.value = body.items || [];
    skillsState.value = "ready";
  } catch (caught) {
    if (isForbidden(caught)) {
      skillsState.value = "hidden";
      return;
    }
    skillsState.value = "error";
    skillsError.value = errorText(caught, "无法读取 Skill");
  }
}

async function loadArtifacts() {
  artifactsState.value = "loading";
  artifactsError.value = "";
  try {
    const body = await getJson("/api/v1/artifacts");
    artifacts.value = body.items || [];
    artifactsState.value = "ready";
  } catch (caught) {
    if (isForbidden(caught)) {
      artifactsState.value = "hidden";
      return;
    }
    artifactsState.value = "error";
    artifactsError.value = errorText(caught, "无法读取制品目录");
  }
}
</script>

<template>
  <PageIntro title="概览" lead="进行中的构建、Skill 草稿和最近制品。" :note="note" />
  <section v-if="metricCount" class="metrics" :style="{ '--n': metricCount }">
    <RouterLink v-if="showBuilds && buildsState !== 'hidden'" class="metric" to="/builds">
      <span class="v">{{ buildsState === "ready" || buildsState === "empty" ? runningCount : "—" }}</span>
      <span class="k">进行中的构建</span>
    </RouterLink>
    <RouterLink v-if="showSkills && skillsState !== 'hidden'" class="metric" to="/skills">
      <span class="v">{{ skillsState === "ready" ? skillMetric.value : "—" }}</span>
      <span class="k">{{ skillMetric.label }}</span>
    </RouterLink>
    <RouterLink v-if="showArtifacts && artifactsState !== 'hidden'" class="metric" to="/artifacts">
      <span class="v">{{ artifactsState === "ready" ? artifacts.length : "—" }}</span>
      <span class="k">制品</span>
    </RouterLink>
  </section>

  <div v-if="topCount" class="grid-2" :class="{ single: topCount === 1 }">
    <section v-if="showBuilds && buildsState !== 'hidden'" class="panel">
      <div class="panel-head">
        <h2>最近构建</h2>
        <RouterLink class="linkish" to="/builds">全部</RouterLink>
      </div>
      <StatePanel
        :loading="buildsState === 'loading' || buildsState === 'idle'"
        :error="buildsState === 'error' ? buildsError : ''"
        :empty="buildsState === 'empty' || buildsState === 'unconfigured'"
        :empty-text="buildsState === 'unconfigured' ? '执行器未配置' : '还没有构建'"
        @retry="loadBuilds"
      >
        <RouterLink
          v-for="item in builds.slice(0, 5)"
          :key="item.id"
          class="build-item"
          :to="{ name: 'build', params: { id: item.id } }"
        >
          <span>
            <span class="name">{{ item.repository }}</span>
            <span class="muted subline">{{ item.ref }} · {{ item.version }}</span>
          </span>
          <StatusTag :status="item.status" />
        </RouterLink>
      </StatePanel>
    </section>

    <section v-if="showSkills && skillsState !== 'hidden'" class="panel">
      <div class="panel-head">
        <h2>Skill 草稿与审核中</h2>
        <RouterLink class="linkish" to="/skills">全部</RouterLink>
      </div>
      <StatePanel
        :loading="skillsState === 'loading' || skillsState === 'idle'"
        :error="skillsState === 'error' ? skillsError : ''"
        :empty="skillsState === 'ready' && reviewSkills.length === 0"
        :empty-text="skills.length ? '没有草稿或审核中的 Skill' : '还没有 Skill'"
        @retry="loadSkills"
      >
        <table v-if="reviewSkills.length">
          <thead>
            <tr>
              <th>名称</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in reviewSkills" :key="item.id" class="clickable">
              <td>
                <RouterLink class="linkish" :to="{ name: 'skill', params: { id: item.id } }">{{ item.name }}</RouterLink>
                <div class="mono subline">{{ item.version }}</div>
              </td>
              <td class="nowrap"><StatusTag :status="item.status" :label="item.status_label" /></td>
            </tr>
          </tbody>
        </table>
      </StatePanel>
    </section>
  </div>

  <section v-if="showArtifacts && artifactsState !== 'hidden'" class="panel">
    <div class="panel-head">
      <h2>最近制品</h2>
      <RouterLink class="linkish" to="/artifacts">全部</RouterLink>
    </div>
    <StatePanel
      :loading="artifactsState === 'loading' || artifactsState === 'idle'"
      :error="artifactsState === 'error' ? artifactsError : ''"
      :empty="artifactsState === 'ready' && artifacts.length === 0"
      empty-text="还没有制品"
      @retry="loadArtifacts"
    >
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>名称</th>
              <th>组</th>
              <th>类型</th>
              <th>最新版本</th>
              <th>更新</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in artifacts.slice(0, 5)" :key="item.id" class="clickable">
              <td>
                <RouterLink class="linkish" :to="{ name: 'artifact', params: { id: item.id } }">{{ item.name }}</RouterLink>
              </td>
              <td>{{ item.group || "—" }}</td>
              <td>{{ item.package_type_label }}</td>
              <td class="mono nowrap">{{ latestVersion(item)?.version || "—" }}</td>
              <td class="nowrap">{{ formatTime(latestVersion(item)?.created_at || item.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </StatePanel>
  </section>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";

import { accessReady, account, loadAccess, perms, roleTags } from "./access";
import { requestJson } from "./api";
import AppIcon from "./components/AppIcon.vue";
import { pageCrumb } from "./crumb";
import { canSee } from "./permissions";
import { clearAccount } from "./session";
import { toasts } from "./toast";
import { readApiToken, writeApiToken } from "./token";

const route = useRoute();
const token = ref("");
const healthOk = ref(false);
const healthKnown = ref(false);

const links = [
  { to: "/", route: "overview", label: "概览", icon: "overview" },
  { to: "/builds", route: "builds", label: "构建", icon: "builds" },
  { to: "/artifacts", route: "artifacts", label: "制品", icon: "artifacts" },
  { to: "/skills", route: "skills", label: "Skill", sub: "构建流程", icon: "skills" },
  { to: "/access", route: "access", label: "账号与权限", icon: "access" },
  { to: "/chat", route: "chat", label: "对话", icon: "chat" },
];

const baseCrumbs = {
  overview: "概览",
  builds: "构建",
  build: "构建",
  artifacts: "制品",
  artifact: "制品",
  skills: "Skill",
  skill: "Skill",
  access: "账号与权限",
  chat: "对话",
};

const visibleLinks = computed(() => {
  if (!accessReady.value) {
    return links.filter((item) => item.route === "overview");
  }
  return links.filter((item) => canSee(item.route, perms.value));
});

const crumbText = computed(() => pageCrumb.value || baseCrumbs[route.name] || "");

const displayName = computed(() => account.value?.display_name || account.value?.username || "");

let healthStarted = false;

watch(
  () => route.fullPath,
  () => {
    pageCrumb.value = "";
    if (route.name === "login") {
      return;
    }
    token.value = readApiToken();
    loadAccess().catch(() => {});
    if (!healthStarted) {
      healthStarted = true;
      loadHealth();
    }
  },
  { immediate: true },
);

function navActive(link) {
  if (link.route === "builds") {
    return route.name === "builds" || route.name === "build";
  }
  if (link.route === "artifacts") {
    return route.name === "artifacts" || route.name === "artifact";
  }
  if (link.route === "skills") {
    return route.name === "skills" || route.name === "skill";
  }
  return route.name === link.route;
}

async function loadHealth() {
  try {
    const response = await fetch("/health", { headers: { Accept: "application/json" } });
    if (!response.ok) {
      throw new Error("unreachable");
    }
    const body = await response.json();
    healthOk.value = body?.status === "ok";
  } catch {
    healthOk.value = false;
  } finally {
    healthKnown.value = true;
  }
}

function saveToken() {
  writeApiToken(token.value);
  clearAccount();
  window.location.reload();
}

async function logout() {
  try {
    await requestJson("/api/v1/auth/logout", { method: "POST" });
  } finally {
    writeApiToken("");
    clearAccount();
    window.location.assign("/login");
  }
}
</script>

<template>
  <RouterView v-if="route.name === 'login'" />
  <div v-else class="shell">
    <aside class="sidebar">
      <RouterLink class="brand-lockup" to="/" aria-label="kiln">
        <span class="mark-wrap" aria-hidden="true">
          <AppIcon name="mark" :size="22" />
        </span>
        <span class="titles">
          <p class="brand">kiln</p>
          <p class="brand-note">制品构建平台</p>
        </span>
      </RouterLink>
      <nav class="nav" aria-label="主导航">
        <RouterLink
          v-for="link in visibleLinks"
          :key="link.to"
          :to="link.to"
          active-class=""
          exact-active-class=""
          :class="{ active: navActive(link) }"
        >
          <span class="nav-ico">
            <AppIcon :name="link.icon" />
          </span>
          <span class="nav-item">
            {{ link.label }}
            <span v-if="link.sub" class="sub">{{ link.sub }}</span>
          </span>
        </RouterLink>
      </nav>
      <div class="sidebar-foot">
        <p v-if="displayName" class="who">{{ displayName }}</p>
        <p v-if="roleTags.length" class="role-row">
          <span v-for="name in roleTags" :key="name" class="tag">{{ name }}</span>
        </p>
        <button type="button" class="logout" @click="logout">退出</button>
        <details class="dev-panel">
          <summary>开发者</summary>
          <form @submit.prevent="saveToken">
            <label for="api-token">机器访问令牌</label>
            <input id="api-token" v-model="token" type="password" autocomplete="off" />
            <button type="submit" class="btn btn-secondary">保存</button>
            <p class="token-note">
              登录后，控制台通过会话访问 /api。这里的令牌是机器和开发用的 KILN_API_TOKEN，不是主要入口。本地示例是 kiln-local，不能用于生产。
            </p>
          </form>
        </details>
      </div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <div class="crumb">
          <b>{{ crumbText }}</b>
        </div>
        <div class="topbar-right">
          <p class="health" :class="{ ok: healthKnown && healthOk, down: healthKnown && !healthOk }">
            <i aria-hidden="true"></i>
            <span>{{ !healthKnown ? "正在检查 API" : healthOk ? "API 正常" : "API 不可达" }}</span>
          </p>
        </div>
      </header>
      <main class="content" :class="{ chat: route.name === 'chat' }">
        <RouterView />
      </main>
      <footer class="footnote">kiln 一期 · 数据保存在 PostgreSQL · 不部署到运行环境</footer>
    </div>
    <div v-if="toasts.length" class="toast-wrap" aria-live="polite">
      <div v-for="item in toasts" :key="item.id" class="toast">{{ item.text }}</div>
    </div>
  </div>
</template>

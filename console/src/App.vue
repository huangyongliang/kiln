<script setup>
import { onMounted, ref } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";

import { requestJson } from "./api";
import { readApiToken, writeApiToken } from "./token";

const links = [
  { to: "/", label: "概览" },
  { to: "/builds", label: "构建" },
  { to: "/artifacts", label: "制品" },
  { to: "/skills", label: "Skill" },
  { to: "/access", label: "账号与权限" },
  { to: "/chat", label: "对话" },
];
const route = useRoute();
const token = ref("");

onMounted(() => {
  token.value = readApiToken();
});

function saveToken() {
  writeApiToken(token.value);
  window.location.reload();
}

async function logout() {
  try {
    await requestJson("/api/v1/auth/logout", { method: "POST" });
  } finally {
    writeApiToken("");
    window.location.assign("/login");
  }
}
</script>

<template>
  <RouterView v-if="route.name === 'login'" />
  <div v-else class="shell">
    <aside class="sidebar">
      <p class="brand">kiln</p>
      <p class="brand-note">制品构建平台</p>
      <nav class="nav" aria-label="主导航">
        <RouterLink v-for="link in links" :key="link.to" :to="link.to" exact-active-class="active">
          {{ link.label }}
        </RouterLink>
      </nav>
      <button type="button" class="logout" @click="logout">退出</button>
      <details class="token-form">
        <summary>机器访问令牌</summary>
        <form @submit.prevent="saveToken">
          <label for="api-token">访问令牌</label>
          <input id="api-token" v-model="token" type="password" autocomplete="off" />
          <button type="submit">保存</button>
          <p class="token-note">
            登录后，控制台通过会话访问 /api。这里的令牌是机器和开发用的 KILN_API_TOKEN，不是主要入口。本地示例是 kiln-local，不能用于生产。
          </p>
        </form>
      </details>
    </aside>
    <div class="workspace">
      <main class="content">
        <RouterView />
      </main>
      <footer class="footnote">一期骨架 · 领域数据保存在 PostgreSQL · 不部署到运行环境</footer>
    </div>
  </div>
</template>

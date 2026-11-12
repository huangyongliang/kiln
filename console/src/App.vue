<script setup>
import { onMounted, ref } from "vue";
import { RouterLink, RouterView } from "vue-router";

import { readApiToken, writeApiToken } from "./token";

const links = [
  { to: "/", label: "概览" },
  { to: "/builds", label: "构建" },
  { to: "/artifacts", label: "制品" },
  { to: "/skills", label: "Skill" },
  { to: "/access", label: "账号与权限" },
  { to: "/chat", label: "对话" },
];
const token = ref("");

onMounted(() => {
  token.value = readApiToken();
});

function saveToken() {
  writeApiToken(token.value);
  window.location.reload();
}
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <p class="brand">kiln</p>
      <p class="brand-note">制品构建平台</p>
      <nav class="nav" aria-label="主导航">
        <RouterLink v-for="link in links" :key="link.to" :to="link.to" exact-active-class="active">
          {{ link.label }}
        </RouterLink>
      </nav>
      <form class="token-form" @submit.prevent="saveToken">
        <label for="api-token">访问令牌</label>
        <input id="api-token" v-model="token" type="password" autocomplete="off" />
        <button type="submit">保存</button>
        <p class="token-note">
          调用 /api 时放在 Authorization 头。本地示例是 kiln-local，不能用于生产。这不是登录。
        </p>
      </form>
    </aside>
    <div class="workspace">
      <main class="content">
        <RouterView />
      </main>
      <footer class="footnote">一期骨架 · 领域数据保存在 PostgreSQL · 不部署到运行环境</footer>
    </div>
  </div>
</template>

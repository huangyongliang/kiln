<script setup>
import { RouterLink, RouterView } from "vue-router";

const links = [
  { to: "/", label: "概览" },
  { to: "/artifacts", label: "制品" },
  { to: "/skills", label: "Skill" },
  { to: "/access", label: "账号与权限" },
  { to: "/chat", label: "对话" },
];
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
    </aside>
    <div class="workspace">
      <main class="content">
        <RouterView />
      </main>
      <footer class="footnote">一期骨架 · 领域数据只保存在 API 进程内存中</footer>
    </div>
  </div>
</template>

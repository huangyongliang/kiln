<script setup>
import { onMounted, ref } from "vue";

import { getJson } from "../api";
import PageIntro from "../components/PageIntro.vue";

const health = ref(null);
const error = ref("");

onMounted(async () => {
  try {
    health.value = await getJson("/health");
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "无法连接 API";
  }
});
</script>

<template>
  <PageIntro
    title="概览"
    lead="这是 kiln 的一期控制台骨架。页面用于确认后端可访问，并进入制品、skill、账号和对话的占位视图。"
  />
  <section class="panel">
    <h2>健康检查</h2>
    <p v-if="error" class="message error">{{ error }}。请先在仓库根目录启动 API。</p>
    <dl v-else-if="health">
      <dt>状态</dt>
      <dd>{{ health.status }}</dd>
      <dt>服务</dt>
      <dd>{{ health.service }}</dd>
      <dt>版本</dt>
      <dd>{{ health.version }}</dd>
    </dl>
    <p v-else class="message">正在读取 /health …</p>
  </section>
  <section class="panel">
    <h2>本地 API</h2>
    <p class="message">直接运行后端时，接口说明在 http://127.0.0.1:8000/docs 。</p>
  </section>
</template>

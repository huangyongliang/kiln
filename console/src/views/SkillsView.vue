<script setup>
import { onMounted, ref } from "vue";

import { getJson } from "../api";
import PageIntro from "../components/PageIntro.vue";

const items = ref([]);
const error = ref("");
const loaded = ref(false);

onMounted(async () => {
  try {
    const body = await getJson("/api/v1/skills");
    items.value = body.items;
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "无法读取 skill";
  } finally {
    loaded.value = true;
  }
});
</script>

<template>
  <PageIntro
    title="Skill"
    lead="构建流程用 skill 描述。本期可以创建草稿、提交审核并发布，但步骤只作为数据保存，不会执行，也不会运行 shell。"
  />
  <section class="panel">
    <p v-if="error" class="message error">{{ error }}</p>
    <p v-else-if="loaded && items.length === 0" class="message">
      还没有 skill。创建草稿请调用 API。
    </p>
    <table v-else-if="loaded">
      <thead>
        <tr>
          <th>名称</th>
          <th>版本</th>
          <th>状态</th>
          <th>步骤</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.id">
          <td>{{ item.name }}</td>
          <td>{{ item.version }}</td>
          <td><span class="tag">{{ item.status_label }}</span></td>
          <td>{{ item.steps.length }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else class="message">正在读取 skill …</p>
  </section>
</template>

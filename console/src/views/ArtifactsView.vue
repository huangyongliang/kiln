<script setup>
import { onMounted, ref } from "vue";

import { getJson } from "../api";
import PageIntro from "../components/PageIntro.vue";

const items = ref([]);
const error = ref("");
const loaded = ref(false);

onMounted(async () => {
  try {
    const body = await getJson("/api/v1/artifacts");
    items.value = body.items;
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "无法读取制品目录";
  } finally {
    loaded.value = true;
  }
});

function versionText(artifact) {
  if (!artifact.versions.length) {
    return "尚无版本";
  }
  return artifact.versions.map((item) => item.version).join("、");
}
</script>

<template>
  <PageIntro
    title="制品"
    lead="平台自管目录、不可变版本和校验和。下载预约与稳定取包接口已在 API 中留出大纲。本期不接收文件字节，也不连接制品仓库。"
  />
  <section class="panel">
    <p v-if="error" class="message error">{{ error }}</p>
    <p v-else-if="loaded && items.length === 0" class="message">
      还没有制品。登记请调用 API。目录写在 PostgreSQL 里，只重启 API 进程不会清空。
    </p>
    <table v-else-if="loaded">
      <thead>
        <tr>
          <th>名称</th>
          <th>组</th>
          <th>类型</th>
          <th>版本</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.id">
          <td>{{ item.name }}</td>
          <td>{{ item.group || "—" }}</td>
          <td>{{ item.package_type_label }}</td>
          <td>{{ versionText(item) }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else class="message">正在读取制品目录 …</p>
  </section>
</template>

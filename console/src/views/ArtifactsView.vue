<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";

import { accessReady, perms } from "../access";
import { errorText, getJson, isForbidden } from "../api";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import StatusTag from "../components/StatusTag.vue";
import { latestVersion } from "../format";
import { canSee } from "../permissions";

const note =
  "平台自管目录、不可变版本和制品字节。字节保存在平台本地存储。版本入库后可以取回这些字节。Nexus 集成还没有接上。";

const items = ref([]);
const mode = ref("loading");
const error = ref("");
const forbidden = ref(false);
const query = ref("");
const typeFilter = ref("全部");

const types = computed(() => {
  const values = new Set(items.value.map((item) => item.package_type_label).filter(Boolean));
  return ["全部", ...values];
});

const filtered = computed(() => {
  const needle = query.value.trim().toLowerCase();
  return items.value.filter((item) => {
    if (typeFilter.value !== "全部" && item.package_type_label !== typeFilter.value) {
      return false;
    }
    if (!needle) {
      return true;
    }
    return [item.name, item.group, item.package_type_label, latestVersion(item)?.version]
      .join(" ")
      .toLowerCase()
      .includes(needle);
  });
});

onMounted(load);
watch(accessReady, (ready) => {
  if (ready) {
    load();
  }
});

async function load() {
  forbidden.value = false;
  if (accessReady.value && !canSee("artifacts", perms.value)) {
    forbidden.value = true;
    return;
  }
  mode.value = "loading";
  error.value = "";
  try {
    const body = await getJson("/api/v1/artifacts");
    items.value = body.items || [];
    mode.value = "ready";
  } catch (caught) {
    if (isForbidden(caught)) {
      forbidden.value = true;
      return;
    }
    mode.value = "error";
    error.value = errorText(caught, "无法读取制品目录");
  }
}

function byteStatus(artifact) {
  const latest = latestVersion(artifact);
  if (latest?.content_available) {
    return "stored";
  }
  return "missing";
}
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="artifact.read" />
  <template v-else>
    <PageIntro title="制品" lead="平台自管目录里的不可变版本。" :note="note" />
    <section class="panel">
      <div class="toolbar">
        <input v-model="query" class="search" type="search" placeholder="搜索名称或组" />
        <button
          v-for="type in types"
          :key="type"
          type="button"
          class="chip"
          :class="{ active: typeFilter === type }"
          @click="typeFilter = type"
        >
          {{ type }}
        </button>
      </div>
      <StatePanel
        :loading="mode === 'loading'"
        :error="mode === 'error' ? error : ''"
        :empty="mode === 'ready' && filtered.length === 0"
        :empty-text="items.length ? '没有符合条件的制品' : '还没有制品'"
        @retry="load"
      >
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>名称</th>
                <th>组</th>
                <th>类型</th>
                <th>最新版本</th>
                <th>版本数</th>
                <th>字节</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in filtered" :key="item.id">
                <td>
                  <RouterLink class="linkish" :to="`/artifacts/${item.id}`">{{ item.name }}</RouterLink>
                </td>
                <td>{{ item.group || "—" }}</td>
                <td>{{ item.package_type_label }}</td>
                <td class="mono nowrap">{{ latestVersion(item)?.version || "—" }}</td>
                <td class="nowrap">{{ item.versions.length }}</td>
                <td class="nowrap">
                  <StatusTag
                    :status="byteStatus(item) === 'stored' ? 'success' : 'unknown'"
                    :label="byteStatus(item) === 'stored' ? '已入库' : '尚无字节'"
                  />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </StatePanel>
    </section>
  </template>
</template>

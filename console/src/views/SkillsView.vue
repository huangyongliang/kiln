<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";

import { accessReady, perms } from "../access";
import { errorText, getJson, isForbidden } from "../api";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import StatusTag from "../components/StatusTag.vue";
import { canSee } from "../permissions";

const note = "步骤只作为数据保存，不会执行，也不会运行 shell。";

const items = ref([]);
const mode = ref("loading");
const error = ref("");
const forbidden = ref(false);
const statusFilter = ref("all");

const filters = [
  ["all", "全部"],
  ["draft", "草稿"],
  ["in_review", "审核中"],
  ["published", "已发布"],
];

const filtered = computed(() => {
  if (statusFilter.value === "all") {
    return items.value;
  }
  return items.value.filter((item) => item.status === statusFilter.value);
});

onMounted(load);
watch(accessReady, (ready) => {
  if (ready) {
    load();
  }
});

async function load() {
  forbidden.value = false;
  if (accessReady.value && !canSee("skills", perms.value)) {
    forbidden.value = true;
    return;
  }
  mode.value = "loading";
  error.value = "";
  try {
    const body = await getJson("/api/v1/skills");
    items.value = body.items || [];
    mode.value = "ready";
  } catch (caught) {
    if (isForbidden(caught)) {
      forbidden.value = true;
      return;
    }
    mode.value = "error";
    error.value = errorText(caught, "无法读取 Skill");
  }
}
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="skill.read" />
  <template v-else>
    <PageIntro title="Skill" lead="Skill 描述构建流程：起草、提交审核、发布。" :note="note" />
    <section class="panel">
      <div class="toolbar">
        <button
          v-for="item in filters"
          :key="item[0]"
          type="button"
          class="chip"
          :class="{ active: statusFilter === item[0] }"
          @click="statusFilter = item[0]"
        >
          {{ item[1] }}
        </button>
      </div>
      <StatePanel
        :loading="mode === 'loading'"
        :error="mode === 'error' ? error : ''"
        :empty="mode === 'ready' && filtered.length === 0"
        :empty-text="items.length ? '没有这个状态的 Skill' : '还没有 Skill'"
        @retry="load"
      >
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>名称</th>
                <th>版本</th>
                <th>状态</th>
                <th>步骤数</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in filtered" :key="item.id">
                <td>
                  <RouterLink class="linkish" :to="{ name: 'skill', params: { id: item.id } }">{{ item.name }}</RouterLink>
                </td>
                <td class="mono nowrap">{{ item.version }}</td>
                <td class="nowrap">
                  <StatusTag :status="item.status" :label="item.status_label" />
                  <span v-if="item.status === 'draft' && item.review_note" class="tag">已退回</span>
                </td>
                <td class="nowrap">{{ item.steps.length }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </StatePanel>
    </section>
  </template>
</template>

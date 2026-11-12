<script setup>
import { onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import { accessReady, perms } from "../access";
import { errorText, getJson, isForbidden } from "../api";
import AppIcon from "../components/AppIcon.vue";
import CopyButton from "../components/CopyButton.vue";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import StatusTag from "../components/StatusTag.vue";
import { pageCrumb } from "../crumb";
import { formatBytes, formatTime, shortDigest, sourceLabel } from "../format";
import { apiPath, isUuid } from "../paths";
import { canSee } from "../permissions";

const route = useRoute();
const artifact = ref(null);
const mode = ref("loading");
const error = ref("");
const forbidden = ref(false);
const missing = ref(false);

onMounted(load);
watch(
  () => route.params.id,
  () => load(),
);
watch(accessReady, (ready) => {
  if (ready) {
    load();
  }
});

async function load() {
  forbidden.value = false;
  missing.value = false;
  const id = route.params.id;
  if (!isUuid(id)) {
    artifact.value = null;
    missing.value = true;
    mode.value = "ready";
    pageCrumb.value = "制品";
    return;
  }
  if (accessReady.value && !canSee("artifact", perms.value)) {
    forbidden.value = true;
    return;
  }
  mode.value = "loading";
  error.value = "";
  try {
    artifact.value = await getJson(apiPath`/api/v1/artifacts/${id}`);
    pageCrumb.value = `制品 / ${artifact.value.name}`;
    mode.value = "ready";
  } catch (caught) {
    if (isForbidden(caught)) {
      forbidden.value = true;
      return;
    }
    mode.value = "error";
    error.value = errorText(caught, "无法读取制品");
  }
}
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="artifact.read" />
  <template v-else>
    <PageIntro :title="artifact?.name || '制品'" lead="版本入库之后不可修改。">
      <template v-if="artifact" #meta>
        <p class="meta-row">
          <span class="tag draft">{{ artifact.package_type_label }}</span>
          <span v-if="artifact.group" class="muted">{{ artifact.group }}</span>
        </p>
      </template>
    </PageIntro>
    <p v-if="artifact?.description" class="prose">{{ artifact.description }}</p>
    <StatePanel
      :loading="mode === 'loading'"
      :error="mode === 'error' ? error : ''"
      :empty="missing"
      empty-text="不存在"
      @retry="load"
    >
      <section v-if="artifact" class="panel">
        <div class="panel-head">
          <h2>版本</h2>
          <p class="lock-note">
            <AppIcon name="lock" :size="16" />
            版本不可变
          </p>
        </div>
        <StatePanel :empty="artifact.versions.length === 0" empty-text="尚无版本">
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>版本</th>
                  <th>校验</th>
                  <th>大小</th>
                  <th>来源</th>
                  <th>字节</th>
                  <th>入库时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in artifact.versions" :key="item.version">
                  <td class="mono nowrap">{{ item.version }}</td>
                  <td class="nowrap">
                    <span class="mono">{{ item.checksum.algorithm }} {{ shortDigest(item.checksum.value) }}</span>
                    <CopyButton :value="item.checksum.value" />
                  </td>
                  <td class="nowrap">{{ formatBytes(item.size_bytes) }}</td>
                  <td>{{ sourceLabel(item.source) }}</td>
                  <td class="nowrap">
                    <StatusTag
                      :status="item.content_available ? 'success' : 'unknown'"
                      :label="item.content_available ? '已入库' : '尚无字节'"
                    />
                  </td>
                  <td class="nowrap">{{ formatTime(item.created_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </StatePanel>
      </section>
    </StatePanel>
  </template>
</template>

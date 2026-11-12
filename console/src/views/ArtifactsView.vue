<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRouter } from "vue-router";

import { accessReady, actionDeniedText, perms } from "../access";
import { errorText, getJson, isForbidden, postJson } from "../api";
import FormDialog from "../components/FormDialog.vue";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import StatusTag from "../components/StatusTag.vue";
import { artifactNameError, boundError, fieldErrors, groupError, packageTypeError } from "../forms";
import { latestVersion } from "../format";
import { canDo, canSee } from "../permissions";

const note =
  "平台自管目录、不可变版本和制品字节。字节保存在平台本地存储。版本入库后可以取回这些字节。Nexus 集成还没有接上。";

const PACKAGE_OPTIONS = [
  ["maven", "Maven"],
  ["python", "Python"],
  ["generic", "通用包"],
  ["frontend", "前端构建包"],
];

const router = useRouter();
const items = ref([]);
const mode = ref("loading");
const error = ref("");
const forbidden = ref(false);
const query = ref("");
const typeFilter = ref("全部");
const createOpen = ref(false);
const createBusy = ref(false);
const createError = ref("");
const createErrors = ref({});
const name = ref("");
const group = ref("");
const packageType = ref("generic");
const description = ref("");

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

function openCreate() {
  name.value = "";
  group.value = "";
  packageType.value = "generic";
  description.value = "";
  createError.value = "";
  createErrors.value = {};
  createOpen.value = true;
}

async function submitArtifact() {
  if (createBusy.value) {
    return;
  }
  const local = {};
  const nameMessage = artifactNameError(name.value.trim());
  if (nameMessage) {
    local.name = nameMessage;
  }
  const groupMessage = groupError(group.value);
  if (groupMessage) {
    local.group = groupMessage;
  }
  const typeMessage = packageTypeError(packageType.value);
  if (typeMessage) {
    local.package_type = typeMessage;
  }
  const descriptionMessage = boundError(description.value, 2000, "说明过长");
  if (descriptionMessage) {
    local.description = descriptionMessage;
  }
  createErrors.value = local;
  createError.value = "";
  if (Object.keys(local).length) {
    return;
  }
  const payload = {
    name: name.value.trim(),
    package_type: packageType.value,
    description: description.value.trim(),
  };
  const groupText = group.value.trim();
  if (groupText) {
    payload.group = groupText;
  }
  createBusy.value = true;
  try {
    const created = await postJson("/api/v1/artifacts", payload);
    createOpen.value = false;
    await router.push({ name: "artifact", params: { id: created.id } });
  } catch (caught) {
    if (isForbidden(caught)) {
      createError.value = actionDeniedText("artifact.upload");
      return;
    }
    const mapped = fieldErrors(caught?.detail);
    if (caught?.status === 409) {
      mapped.name = mapped.name || caught.message;
    }
    if (Object.keys(mapped).length) {
      createErrors.value = mapped;
      return;
    }
    createError.value = errorText(caught, "无法登记制品");
  } finally {
    createBusy.value = false;
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
    <PageIntro title="制品" lead="平台自管目录里的不可变版本。" :note="note">
      <template v-if="canDo('artifact.create', perms)" #actions>
        <button type="button" class="btn" @click="openCreate">登记制品</button>
      </template>
    </PageIntro>
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
                  <RouterLink class="linkish" :to="{ name: 'artifact', params: { id: item.id } }">{{ item.name }}</RouterLink>
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
    <FormDialog
      v-if="createOpen"
      title="登记制品"
      :busy="createBusy"
      :error="createError"
      @close="createOpen = false"
      @submit="submitArtifact"
    >
      <div class="field">
        <label for="artifact-name">名称</label>
        <input id="artifact-name" v-model="name" type="text" autocomplete="off" />
        <p v-if="createErrors.name" class="field-error">{{ createErrors.name }}</p>
      </div>
      <div class="field">
        <label for="artifact-group">组</label>
        <input id="artifact-group" v-model="group" type="text" autocomplete="off" />
        <p v-if="createErrors.group" class="field-error">{{ createErrors.group }}</p>
      </div>
      <div class="field">
        <label for="artifact-type">类型</label>
        <select id="artifact-type" v-model="packageType">
          <option v-for="item in PACKAGE_OPTIONS" :key="item[0]" :value="item[0]">{{ item[1] }}</option>
        </select>
        <p v-if="createErrors.package_type" class="field-error">{{ createErrors.package_type }}</p>
      </div>
      <div class="field">
        <label for="artifact-description">说明</label>
        <textarea id="artifact-description" v-model="description" rows="3"></textarea>
        <p v-if="createErrors.description" class="field-error">{{ createErrors.description }}</p>
      </div>
    </FormDialog>
  </template>
</template>

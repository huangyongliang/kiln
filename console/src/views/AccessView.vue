<script setup>
import { computed, onMounted, ref, watch } from "vue";

import { accessReady, perms, roles as catalogRoles } from "../access";
import { errorText, getJson, isForbidden } from "../api";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import StatusTag from "../components/StatusTag.vue";
import { formatTime } from "../format";
import { canSee } from "../permissions";

const lead = "账号与授权都在 kiln 内。公司系统接入用户信息同步后，只同步身份和在职状态。";
const note =
  "平台自身具备完整的账号与权限能力，账号和 RBAC 目录都在本地。现在有登录页，口令只保存哈希。公司用户系统的对接是可选的；可选对接之后，公司系统只同步身份和在职状态，kiln 的授权仍留在平台上。这一版已经接上本地登录，以及用户信息同步的起步。登录后的写入会按角色目录鉴权。发起任务或产品构建需要 executor.run，对话需要 agent.qa。只读角色不能调用这三处写入。对话可在授权下通过工具起草 skill 草稿，或发起与 POST /builds 相同的产品构建。查看账号、制品和 skill 需要对应的读取权限，只读角色已经具备。查看执行器状态、任务、产品构建及其日志需要 executor.read，查看代码源需要 git.read；这两项只授给构建者及以上。机器和开发用的访问令牌仍可使用，并拥有目录中的全部权限。";

const tab = ref("accounts");
const accounts = ref([]);
const permissions = ref([]);
const mode = ref("loading");
const error = ref("");
const forbidden = ref(false);
const openRole = ref("");

const roleNames = computed(() => {
  const names = new Map(catalogRoles.value.map((role) => [role.id, role.name]));
  return (ids) => (ids || []).map((id) => names.get(id)).filter(Boolean);
});

const descriptions = computed(() => {
  const map = new Map(permissions.value.map((item) => [item.code, item.description]));
  return (code) => map.get(code) || code;
});

onMounted(load);
watch(accessReady, (ready) => {
  if (ready) {
    load();
  }
});

async function load() {
  forbidden.value = false;
  if (accessReady.value && !canSee("access", perms.value)) {
    forbidden.value = true;
    return;
  }
  mode.value = "loading";
  error.value = "";
  try {
    const [accountBody, permissionBody] = await Promise.all([
      getJson("/api/v1/accounts"),
      getJson("/api/v1/rbac/permissions"),
    ]);
    accounts.value = accountBody.items || [];
    permissions.value = permissionBody.items || [];
    if (catalogRoles.value.length === 0) {
      const roleBody = await getJson("/api/v1/rbac/roles");
      catalogRoles.value = roleBody.items || [];
    }
    mode.value = "ready";
  } catch (caught) {
    if (isForbidden(caught)) {
      forbidden.value = true;
      return;
    }
    mode.value = "error";
    error.value = errorText(caught, "无法读取账号与权限");
  }
}

function toggleRole(id) {
  openRole.value = openRole.value === id ? "" : id;
}
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="account.read" />
  <template v-else>
    <PageIntro title="账号与权限" :lead="lead" :note="note" />
    <div class="tabs" role="tablist">
      <button type="button" class="chip" :class="{ active: tab === 'accounts' }" @click="tab = 'accounts'">
        账号
      </button>
      <button type="button" class="chip" :class="{ active: tab === 'roles' }" @click="tab = 'roles'">
        角色
      </button>
    </div>
    <section class="panel">
      <StatePanel
        :loading="mode === 'loading'"
        :error="mode === 'error' ? error : ''"
        :empty="mode === 'ready' && tab === 'accounts' && accounts.length === 0"
        empty-text="还没有账号。"
        @retry="load"
      >
        <div v-if="tab === 'accounts'" class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>用户名</th>
                <th>显示名</th>
                <th>角色</th>
                <th>邮箱</th>
                <th>状态</th>
                <th>创建时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in accounts" :key="item.id">
                <td class="mono nowrap">{{ item.username }}</td>
                <td>{{ item.display_name }}</td>
                <td>
                  <span v-for="name in roleNames(item.role_ids)" :key="name" class="tag">{{ name }}</span>
                  <span v-if="roleNames(item.role_ids).length === 0" class="muted">—</span>
                </td>
                <td>{{ item.email || "—" }}</td>
                <td class="nowrap">
                  <StatusTag :status="item.status" :label="item.status === 'active' ? '启用' : '停用'" />
                </td>
                <td class="nowrap">{{ formatTime(item.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>角色</th>
                <th>说明</th>
                <th>权限</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="role in catalogRoles" :key="role.id">
                <tr>
                  <td class="nowrap">{{ role.name }}</td>
                  <td>{{ role.description }}</td>
                  <td class="nowrap">
                    <button type="button" class="btn btn-ghost" @click="toggleRole(role.id)">
                      {{ openRole === role.id ? "收起" : "展开" }}
                    </button>
                  </td>
                </tr>
                <tr v-if="openRole === role.id">
                  <td colspan="3">
                    <div class="tag-wrap">
                      <span v-for="code in role.permissions" :key="code" class="tag">
                        {{ descriptions(code) }}
                      </span>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </StatePanel>
    </section>
  </template>
</template>

<script setup>
import { onMounted, ref } from "vue";

import { getJson } from "../api";
import PageIntro from "../components/PageIntro.vue";

const accounts = ref([]);
const roles = ref([]);
const error = ref("");
const loaded = ref(false);

onMounted(async () => {
  try {
    const [accountBody, roleBody] = await Promise.all([
      getJson("/api/v1/accounts"),
      getJson("/api/v1/rbac/roles"),
    ]);
    accounts.value = accountBody.items;
    roles.value = roleBody.items;
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "无法读取账号与权限";
  } finally {
    loaded.value = true;
  }
});
</script>

<template>
  <PageIntro
    title="账号与权限"
    lead="平台自身具备完整的账号与权限能力，账号和 RBAC 目录都在本地。公司用户系统的对接是可选的；可选对接之后，公司系统只确认身份和在职状态，授权仍留在平台上。这一版没有登录页，也不保存口令。本地登录和身份联邦的起步仍属于一期。调用接口需要侧栏里的访问令牌。下面的角色目录还不参与请求鉴权。"
  />
  <p v-if="error" class="message error">{{ error }}</p>
  <template v-else-if="loaded">
    <section class="panel">
      <h2>本地账号</h2>
      <p v-if="accounts.length === 0" class="message">还没有账号。</p>
      <table v-else>
        <thead>
          <tr>
            <th>用户名</th>
            <th>显示名</th>
            <th>角色</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="account in accounts" :key="account.id">
            <td>{{ account.username }}</td>
            <td>{{ account.display_name }}</td>
            <td>{{ account.role_ids.join("、") }}</td>
          </tr>
        </tbody>
      </table>
    </section>
    <section class="panel">
      <h2>角色</h2>
      <table>
        <thead>
          <tr>
            <th>角色</th>
            <th>说明</th>
            <th>权限数</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="role in roles" :key="role.id">
            <td>{{ role.name }}</td>
            <td>{{ role.description }}</td>
            <td>{{ role.permissions.length }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </template>
  <p v-else class="message">正在读取账号与权限 …</p>
</template>

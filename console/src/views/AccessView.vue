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
    lead="平台自建本地账号和 RBAC 目录。这一版没有登录页，也不保存口令。本地登录和身份联邦的起步仍属于一期，这里还没有接上。调用接口需要侧栏里的访问令牌。下面的角色只说明以后可以授予的能力。"
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

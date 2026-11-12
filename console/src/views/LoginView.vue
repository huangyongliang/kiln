<script setup>
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { getJson, postJson } from "../api";
import PageIntro from "../components/PageIntro.vue";

const router = useRouter();
const username = ref("");
const password = ref("");
const error = ref("");
const submitting = ref(false);
const companySignIn = ref(false);

onMounted(async () => {
  try {
    const status = await getJson("/api/v1/auth/sync");
    companySignIn.value = status.configured === true;
  } catch {
    companySignIn.value = false;
  }
});

async function submit() {
  error.value = "";
  submitting.value = true;
  try {
    await postJson("/api/v1/auth/login", {
      username: username.value.trim(),
      password: password.value,
    });
    await router.push("/");
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "登录失败";
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <main class="login-screen">
    <div class="content">
      <PageIntro
        title="登录"
        lead="使用本地账号登录。口令只以哈希保存在平台里。公司用户系统的对接是可选的；配置用户信息同步之后，这里会显示公司登录。公司系统只同步身份和在职状态，kiln 的授权仍留在平台上。"
      />
      <form class="panel" @submit.prevent="submit">
        <label for="username">用户名</label>
        <input id="username" v-model="username" type="text" autocomplete="username" required />
        <label for="password">口令</label>
        <input
          id="password"
          v-model="password"
          type="password"
          autocomplete="current-password"
          required
        />
        <button type="submit" :disabled="submitting">登录</button>
        <p v-if="error" class="message error">{{ error }}</p>
      </form>
      <p v-if="companySignIn" class="panel">
        <a href="/api/v1/auth/oidc/start">公司登录</a>
      </p>
    </div>
  </main>
</template>

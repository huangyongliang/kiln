<script setup>
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { errorText, getJson, postJson } from "../api";
import AppIcon from "../components/AppIcon.vue";
import { clearAccount, readFlash, writeAccount } from "../session";
import { pushToast, toasts } from "../toast";

const router = useRouter();
const username = ref("");
const password = ref("");
const error = ref("");
const submitting = ref(false);
const companySignIn = ref(false);

const note =
  "使用本地账号登录。口令只以哈希保存在平台里。公司用户系统的对接是可选的；配置用户信息同步之后，这里会显示公司登录。公司系统只同步身份和在职状态，kiln 的授权仍留在平台上。";

onMounted(async () => {
  const flash = readFlash();
  clearAccount();
  if (flash) {
    pushToast(flash);
  }
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
    const body = await postJson("/api/v1/auth/login", {
      username: username.value.trim(),
      password: password.value,
    });
    if (body?.account) {
      writeAccount(body.account);
    }
    await router.push("/");
  } catch (caught) {
    error.value = errorText(caught, "登录失败");
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-hero">
      <div class="brand-lockup">
        <span class="mark-wrap" aria-hidden="true">
          <AppIcon name="mark" :size="22" />
        </span>
        <span class="titles">
          <p class="brand">kiln</p>
          <p class="brand-note">制品构建平台</p>
        </span>
      </div>
      <h1>把流程烧成可追溯的制品。</h1>
    </section>
    <section class="login-main">
      <div class="login-card">
        <p class="login-kicker">登录</p>
        <p class="login-lead">使用本地账号登录。</p>
        <form @submit.prevent="submit">
          <div class="field">
            <label for="username">用户名</label>
            <input id="username" v-model="username" type="text" autocomplete="username" required />
          </div>
          <div class="field">
            <label for="password">口令</label>
            <input
              id="password"
              v-model="password"
              type="password"
              autocomplete="current-password"
              required
            />
          </div>
          <button type="submit" class="btn" :disabled="submitting">
            <span v-if="submitting" class="spinner" aria-hidden="true"></span>
            {{ submitting ? "正在登录…" : "登录" }}
          </button>
          <p v-if="error" class="message error" role="alert">{{ error }}</p>
        </form>
        <details class="note">
          <summary>说明</summary>
          <p>{{ note }}</p>
        </details>
        <template v-if="companySignIn">
          <div class="or" aria-hidden="true"><span>或</span></div>
          <a class="btn btn-secondary" href="/api/v1/auth/oidc/start">使用公司账号登录</a>
          <p class="hint">公司账号通过用户信息同步登录，权限仍由 kiln 分配。</p>
        </template>
      </div>
    </section>
    <div v-if="toasts.length" class="toast-wrap" aria-live="polite">
      <div v-for="item in toasts" :key="item.id" class="toast">{{ item.text }}</div>
    </div>
  </main>
</template>

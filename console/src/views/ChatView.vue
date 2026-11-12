<script setup>
import { onMounted, ref } from "vue";

import { getJson, postJson } from "../api";
import PageIntro from "../components/PageIntro.vue";

const provider = ref(null);
const prompt = ref("");
const reply = ref("");
const error = ref("");
const sending = ref(false);

const providerText = {
  openai: "OpenAI 兼容端点",
  mock: "模拟",
  off: "关闭",
};

onMounted(async () => {
  try {
    provider.value = await getJson("/api/v1/llm/provider");
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "无法读取模型状态";
  }
});

async function send() {
  const content = prompt.value.trim();
  if (!content || sending.value) {
    return;
  }
  sending.value = true;
  reply.value = "";
  error.value = "";
  try {
    const body = await postJson("/api/v1/llm/chat", {
      messages: [{ role: "user", content }],
    });
    reply.value = body.content;
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "发送失败";
  } finally {
    sending.value = false;
  }
}
</script>

<template>
  <PageIntro
    title="对话"
    lead="模型接口固定为 OpenAI 兼容协议，端点由环境变量配置。端点未就绪时使用模拟或关闭模式。对话不能下发任意 shell。"
  />
  <section class="panel">
    <h2>当前提供方</h2>
    <p v-if="provider" class="message">
      {{ providerText[provider.provider] || provider.provider }}
      <template v-if="provider.default_model"> · 默认模型 {{ provider.default_model }}</template>
    </p>
    <p v-else class="message">正在读取模型状态 …</p>
  </section>
  <section class="panel">
    <form @submit.prevent="send">
      <label for="prompt">提示词</label>
      <textarea id="prompt" v-model="prompt" rows="5" placeholder="向已配置的模型发送一条消息" />
      <button type="submit" :disabled="sending || !prompt.trim()">发送</button>
    </form>
    <p v-if="error" class="message error" role="alert">{{ error }}</p>
    <p v-if="reply" class="reply" aria-live="polite">{{ reply }}</p>
  </section>
</template>

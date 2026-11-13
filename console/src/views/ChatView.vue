<script setup>
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";

import { accessReady, actionDeniedText, perms } from "../access";
import { errorText, getJson, isForbidden, postJson } from "../api";
import { chatRequestMessages, readChatThread, writeChatThread } from "../chat";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import { isUuid } from "../paths";
import { canDo, canSee } from "../permissions";

const note =
  "模型接口固定为 OpenAI 兼容协议，端点由环境变量配置。端点未就绪时使用模拟或关闭模式。对话不能下发任意 shell。";

const provider = ref(null);
const messages = ref(readChatThread(window.sessionStorage));
const prompt = ref("");
const error = ref("");
const pageError = ref("");
const sending = ref(false);
const forbidden = ref(false);
const mode = ref("loading");
const listEl = ref(null);

const off = computed(() => provider.value?.provider === "off");
const mock = computed(() => provider.value?.provider === "mock");
const canSend = computed(() => canDo("chat.send", perms.value));
const composerDisabled = computed(() => off.value || !canSend.value || mode.value !== "ready");

const pill = computed(() => {
  if (!provider.value) {
    return "";
  }
  if (provider.value.provider === "openai") {
    return provider.value.default_model
      ? `模型已连接 · ${provider.value.default_model}`
      : "模型已连接";
  }
  if (provider.value.provider === "mock") {
    return "模拟模式";
  }
  return "对话未开启";
});

watch(messages, (value) => writeChatThread(window.sessionStorage, value), { deep: true });

onMounted(load);
watch(accessReady, (ready) => {
  if (ready) {
    load();
  }
});

async function load() {
  forbidden.value = false;
  if (accessReady.value && !canSee("chat", perms.value)) {
    forbidden.value = true;
    return;
  }
  mode.value = "loading";
  pageError.value = "";
  try {
    provider.value = await getJson("/api/v1/llm/provider");
    mode.value = "ready";
  } catch (caught) {
    if (isForbidden(caught)) {
      forbidden.value = true;
      return;
    }
    mode.value = "error";
    pageError.value = errorText(caught, "无法读取模型状态");
  }
}

function suggest(text) {
  if (composerDisabled.value) {
    return;
  }
  prompt.value = text;
}

function onKeydown(event) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    send();
  }
}

async function scrollMessages() {
  await nextTick();
  if (listEl.value) {
    listEl.value.scrollTop = listEl.value.scrollHeight;
  }
}

function actionTarget(action) {
  if (!action || !isUuid(action.id)) {
    return null;
  }
  if (action.type === "skill_drafted") {
    return { name: "skill", params: { id: action.id } };
  }
  if (action.type === "build_started") {
    return { name: "build", params: { id: action.id } };
  }
  return null;
}

function actionLabel(action) {
  if (action.type === "skill_drafted") {
    return `Skill：${action.name}`;
  }
  if (action.type === "build_started") {
    return `构建：${action.name}`;
  }
  return action.name;
}

async function send() {
  const content = prompt.value.trim();
  if (!content || sending.value || composerDisabled.value) {
    return;
  }
  const requestMessages = chatRequestMessages(messages.value, content);
  sending.value = true;
  error.value = "";
  messages.value = [...messages.value, { role: "user", content, failed: false }];
  await scrollMessages();
  try {
    const body = await postJson("/api/v1/llm/chat", { messages: requestMessages });
    const actions = Array.isArray(body?.actions)
      ? body.actions.filter((item) => actionTarget(item))
      : [];
    messages.value = [
      ...messages.value,
      { role: "assistant", content: body?.content || "", actions },
    ];
    prompt.value = "";
    await scrollMessages();
  } catch (caught) {
    const copy = messages.value.slice();
    const last = copy[copy.length - 1];
    if (last && last.role === "user") {
      copy[copy.length - 1] = { ...last, failed: true };
      messages.value = copy;
    }
    error.value = isForbidden(caught) ? actionDeniedText("agent.qa") : errorText(caught, "发送失败");
  } finally {
    sending.value = false;
  }
}
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="agent.qa" />
  <div v-else class="chat-page">
    <PageIntro title="对话" lead="在平台内向已配置的模型提问。" :note="note">
      <template v-if="pill" #actions>
        <span class="pill" :data-provider="provider?.provider">{{ pill }}</span>
      </template>
    </PageIntro>
    <StatePanel :loading="mode === 'loading'" :error="mode === 'error' ? pageError : ''" @retry="load">
      <div class="chat-layout">
        <div ref="listEl" class="messages" aria-live="polite">
          <p v-if="messages.length === 0 && !off" class="empty">发送一条消息。</p>
          <div
            v-for="(item, index) in messages"
            :key="index"
            class="bubble"
            :class="{ user: item.role === 'user', failed: item.failed }"
          >
            <p class="bubble-text">{{ item.content }}</p>
            <span v-if="item.failed" class="fail-mark">发送失败</span>
            <p v-if="item.actions?.length" class="bubble-actions">
              <RouterLink
                v-for="action in item.actions"
                :key="`${action.type}:${action.id}`"
                class="linkish"
                :to="actionTarget(action)"
              >
                {{ actionLabel(action) }}
              </RouterLink>
            </p>
          </div>
        </div>
        <div v-if="mode === 'ready' && !off" class="suggest">
          <button
            v-if="canDo('agent.draft_skill', perms)"
            type="button"
            @click="suggest('起草一个构建流程')"
          >
            起草一个构建流程
          </button>
          <button
            v-if="canDo('agent.build_artifact', perms)"
            type="button"
            @click="suggest('用已发布的 Skill 构建制品')"
          >
            用已发布的 Skill 构建制品
          </button>
        </div>
        <form class="composer" @submit.prevent="send">
          <textarea
            v-model="prompt"
            rows="2"
            :disabled="composerDisabled"
            placeholder="向已配置的模型发送一条消息"
            @keydown="onKeydown"
          />
          <button
            v-if="canSend"
            type="submit"
            class="btn"
            :disabled="composerDisabled || sending || !prompt.trim()"
          >
            <span v-if="sending" class="spinner" aria-hidden="true"></span>
            发送
          </button>
        </form>
        <p v-if="off" class="hint">
          模型未开启（当前为关闭）。配置 OpenAI 兼容端点后，可以问答、起草 Skill，并在 Skill 发布后发起构建。
        </p>
        <p v-else-if="mock" class="hint">模拟模式不调用工具，不能起草 Skill 或发起构建。</p>
        <p class="hint">对话只在平台内操作，不会执行任意 shell。</p>
        <p v-if="error" class="message error" role="alert">{{ error }}</p>
      </div>
    </StatePanel>
  </div>
</template>

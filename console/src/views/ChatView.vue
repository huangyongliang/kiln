<script setup>
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";

import { accessReady, account, actionDeniedText, perms } from "../access";
import { errorText, getJson, isForbidden, postJson } from "../api";
import {
  EMPTY_CATALOG_TIP,
  STALE_MODEL_TIP,
  chatModelState,
  chatRequestBody,
  failOutgoingMessage,
  shouldRefetchProvider,
  readChatModel,
  readChatThread,
  showModelPicker,
  takeUserMessage,
  writeChatModel,
  writeChatThread,
  composerKeyAction,
} from "../chat";
import MarkdownBody from "../components/MarkdownBody.vue";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import StatusTag from "../components/StatusTag.vue";
import { isUuid } from "../paths";
import { canDo, canSee } from "../permissions";
import { canBuildWithSkill, canReviseSkill, skillListPath } from "../skillLists";

const note =
  "模型接口固定为 OpenAI 兼容协议，端点由环境变量配置。端点未就绪时使用模拟或关闭模式。对话不能下发任意 shell。";

const provider = ref(null);
const messages = ref(readChatThread(window.sessionStorage));
const selectedModel = ref("");
const modelNotice = ref("");
const prompt = ref("");
const error = ref("");
const pageError = ref("");
const sending = ref(false);
const forbidden = ref(false);
const mode = ref("loading");
const listEl = ref(null);
const mineItems = ref([]);
const usableItems = ref([]);
const mineMode = ref("idle");
const usableMode = ref("idle");
const mineError = ref("");
const usableError = ref("");

const off = computed(() => provider.value?.provider === "off");
const mock = computed(() => provider.value?.provider === "mock");
const canSend = computed(() => canDo("chat.send", perms.value));
const composerDisabled = computed(() => off.value || !canSend.value || mode.value !== "ready");
const showPicker = computed(() => showModelPicker(provider.value));
const emptyCatalog = computed(
  () => mode.value === "ready" && provider.value?.provider === "openai" && !showPicker.value,
);
const canUseSkills = computed(() => canDo("skill.create", perms.value));
const accountId = computed(() => account.value?.id || "");

const pill = computed(() => {
  if (!provider.value) {
    return "";
  }
  if (provider.value.provider === "openai") {
    return "模型已连接";
  }
  if (provider.value.provider === "mock") {
    return "模拟模式";
  }
  return "对话未开启";
});

watch(messages, (value) => writeChatThread(window.sessionStorage, value), { deep: true });
watch(provider, syncModelSelection);

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
  const sidebar = accessReady.value ? loadSidebar() : Promise.resolve();
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
  await sidebar;
}

async function loadSidebar() {
  await Promise.all([loadMine(), loadUsable()]);
}

async function loadMine() {
  mineMode.value = "loading";
  mineError.value = "";
  try {
    const body = await getJson(skillListPath("mine"));
    mineItems.value = Array.isArray(body?.items) ? body.items : [];
    mineMode.value = "ready";
  } catch (caught) {
    mineMode.value = "error";
    mineError.value = errorText(caught, "无法读取我产出的 Skill");
  }
}

async function loadUsable() {
  if (!canUseSkills.value) {
    usableItems.value = [];
    usableMode.value = "hidden";
    usableError.value = "";
    return;
  }
  usableMode.value = "loading";
  usableError.value = "";
  try {
    const body = await getJson(skillListPath("usable"));
    usableItems.value = Array.isArray(body?.items) ? body.items : [];
    usableMode.value = "ready";
  } catch (caught) {
    if (isForbidden(caught)) {
      usableItems.value = [];
      usableMode.value = "hidden";
      return;
    }
    usableMode.value = "error";
    usableError.value = errorText(caught, "无法读取已发布 Skill");
  }
}

function syncModelSelection() {
  const state = chatModelState(provider.value, readChatModel(window.sessionStorage));
  selectedModel.value = state.model;
  if (!state.showPicker) {
    modelNotice.value = "";
  } else if (state.fellBack) {
    modelNotice.value = STALE_MODEL_TIP;
  }
  if (state.model) {
    writeChatModel(window.sessionStorage, state.model);
  }
}

async function refetchDisallowedModel(rejected) {
  try {
    const fresh = await getJson("/api/v1/llm/provider");
    const state = chatModelState(fresh, rejected);
    if (state.model) {
      writeChatModel(window.sessionStorage, state.model);
    }
    provider.value = fresh;
    selectedModel.value = state.model;
    modelNotice.value = state.showPicker && state.fellBack ? STALE_MODEL_TIP : state.notice;
  } catch {
    // The failed bubble and the 400 text stay either way.
  }
}

function onModelChange() {
  writeChatModel(window.sessionStorage, selectedModel.value);
  modelNotice.value = "";
}

function suggest(text) {
  if (composerDisabled.value) {
    return;
  }
  prompt.value = text;
}

function onKeydown(event) {
  if (composerKeyAction(event) !== "send") {
    return;
  }
  event.preventDefault();
  send();
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
  if (action.type === "skill_drafted" || action.type === "skill_updated") {
    return { name: "skill", params: { id: action.id } };
  }
  if (action.type === "build_started") {
    return { name: "build", params: { id: action.id } };
  }
  return null;
}

function actionLabel(action) {
  if (action.type === "skill_drafted" || action.type === "skill_updated") {
    return `Skill：${action.name}`;
  }
  if (action.type === "build_started") {
    return `构建：${action.name}`;
  }
  return action.name;
}

function canEditItem(item) {
  return canReviseSkill(item, perms.value, accountId.value);
}

function canBuildItem(item) {
  return canBuildWithSkill(item, perms.value);
}

async function send() {
  if (sending.value || composerDisabled.value) {
    return;
  }
  const started = takeUserMessage(prompt.value, messages.value);
  if (!started) {
    return;
  }
  // Capture the id now. Changing the select while this request is in flight
  // applies only to the next send, and does not rewrite bubbles already shown.
  const outboundModel = showPicker.value ? selectedModel.value : "";
  const payload = chatRequestBody(messages.value, started.content, outboundModel);
  sending.value = true;
  error.value = "";
  messages.value = started.messages;
  prompt.value = started.prompt;
  await scrollMessages();
  try {
    const body = await postJson("/api/v1/llm/chat", payload);
    const actions = Array.isArray(body?.actions)
      ? body.actions.filter((item) => actionTarget(item))
      : [];
    const reply = { role: "assistant", content: body?.content || "", actions };
    if (typeof body?.model === "string" && body.model.length > 0) {
      reply.model = body.model;
    }
    messages.value = [...messages.value, reply];
    if (actions.some((item) => item.type === "skill_drafted" || item.type === "skill_updated")) {
      loadSidebar();
    }
    await scrollMessages();
  } catch (caught) {
    const failed = failOutgoingMessage(messages.value, prompt.value);
    messages.value = failed.messages;
    prompt.value = failed.prompt;
    if (shouldRefetchProvider(caught)) {
      await refetchDisallowedModel(outboundModel);
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
    <div class="chat-body">
      <aside class="skill-rail" aria-label="Skill 列表">
        <section>
          <h2>我产出的</h2>
          <p v-if="mineMode === 'loading'" class="hint">正在读取…</p>
          <p v-else-if="mineMode === 'error'" class="message error" role="alert">
            {{ mineError }}
            <button type="button" class="btn btn-ghost" @click="loadMine">重试</button>
          </p>
          <p v-else-if="mineItems.length === 0" class="hint">还没有你产出的 Skill。</p>
          <ul v-else>
            <li v-for="item in mineItems" :key="item.id">
              <RouterLink class="linkish" :to="{ name: 'skill', params: { id: item.id } }">
                {{ item.name }}
              </RouterLink>
              <span class="mono">{{ item.version }}</span>
              <p class="rail-status">
                <StatusTag :status="item.status" :label="item.status_label" />
                <span v-if="item.status === 'draft' && item.review_note" class="tag">已退回</span>
              </p>
              <p class="rail-links">
                <RouterLink class="linkish" :to="{ name: 'skill', params: { id: item.id } }">详情</RouterLink>
                <RouterLink
                  v-if="canEditItem(item)"
                  class="linkish"
                  :to="{ name: 'skill', params: { id: item.id }, query: { edit: '1' } }"
                >
                  编辑
                </RouterLink>
                <RouterLink
                  v-if="canBuildItem(item)"
                  class="linkish"
                  :to="{ name: 'builds', query: { skill: item.id } }"
                >
                  构建
                </RouterLink>
              </p>
            </li>
          </ul>
        </section>
        <section v-if="usableMode !== 'hidden'">
          <h2>我能用的已发布 Skill</h2>
          <p v-if="usableMode === 'loading' || usableMode === 'idle'" class="hint">正在读取…</p>
          <p v-else-if="usableMode === 'error'" class="message error" role="alert">
            {{ usableError }}
            <button type="button" class="btn btn-ghost" @click="loadUsable">重试</button>
          </p>
          <p v-else-if="usableItems.length === 0" class="hint">还没有可使用的已发布 Skill。</p>
          <ul v-else>
            <li v-for="item in usableItems" :key="item.id">
              <RouterLink class="linkish" :to="{ name: 'skill', params: { id: item.id } }">
                {{ item.name }}
              </RouterLink>
              <span class="mono">{{ item.version }}</span>
              <p class="rail-status">
                <StatusTag :status="item.status" :label="item.status_label" />
              </p>
              <p class="rail-links">
                <RouterLink class="linkish" :to="{ name: 'skill', params: { id: item.id } }">详情</RouterLink>
                <RouterLink
                  v-if="canBuildItem(item)"
                  class="linkish"
                  :to="{ name: 'builds', query: { skill: item.id } }"
                >
                  构建
                </RouterLink>
              </p>
            </li>
          </ul>
        </section>
      </aside>
      <div class="chat-main">
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
                <MarkdownBody :text="item.content" />
                <span v-if="item.role === 'assistant' && item.model" class="bubble-model">{{ item.model }}</span>
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
              <button type="button" @click="suggest('看看我的 Skill')">看看我的 Skill</button>
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
              <div class="composer-actions">
                <select
                  v-if="showPicker"
                  v-model="selectedModel"
                  class="model-select"
                  aria-label="模型"
                  :disabled="!canSend"
                  @change="onModelChange"
                >
                  <option v-for="id in provider.models" :key="id" :value="id">{{ id }}</option>
                </select>
                <button
                  v-if="canSend"
                  type="submit"
                  class="btn"
                  :disabled="composerDisabled || sending || !prompt.trim()"
                >
                  <span v-if="sending" class="spinner" aria-hidden="true"></span>
                  发送
                </button>
              </div>
            </form>
            <p v-if="modelNotice" class="hint">{{ modelNotice }}</p>
            <p v-else-if="emptyCatalog" class="hint">{{ EMPTY_CATALOG_TIP }}</p>
            <p v-if="off" class="hint">
              模型未开启（当前为关闭）。配置 OpenAI 兼容端点后，可以问答、起草 Skill，并在 Skill 发布后发起构建。
              <template v-if="canDo('skill.create', perms)">
                现在可以用表单
                <RouterLink :to="{ name: 'skill-new' }">新建草稿</RouterLink>
                。
              </template>
            </p>
            <p v-else-if="mock" class="hint">模拟模式不调用工具，不能起草 Skill 或发起构建。</p>
            <p class="hint">对话只在平台内操作，不会执行任意 shell。</p>
            <p v-if="error" class="message error" role="alert">{{ error }}</p>
          </div>
        </StatePanel>
      </div>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";

import { accessReady, perms } from "../access";
import { createChatSession } from "../chatSession";
import { DELETE_CHAT_WARNING, EMPTY_CATALOG_TIP, INTERRUPTED_MARK, composerKeyAction } from "../chat";
import MarkdownBody from "../components/MarkdownBody.vue";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import StatusTag from "../components/StatusTag.vue";
import { isUuid } from "../paths";
import { canDo } from "../permissions";
import { canBuildWithSkill, canReviseSkill } from "../skillLists";

const note =
  "模型接口固定为 OpenAI 兼容协议，端点由环境变量配置。端点未就绪时使用模拟或关闭模式。对话不能下发任意 shell。";

const listEl = ref(null);
const cancelDeleteButton = ref(null);
const session = createChatSession({
  localStorage: window.localStorage,
  sessionStorage: window.sessionStorage,
  async afterTurn() {
    await nextTick();
    if (listEl.value) {
      listEl.value.scrollTop = listEl.value.scrollHeight;
    }
  },
});

const {
  messages,
  prompt,
  error,
  pageError,
  modelNotice,
  interruptNotice,
  sending,
  forbidden,
  mode,
  statusRecovery,
  conversations,
  activeId,
  listNotice,
  menuConversationId,
  deleteConfirmOpen,
  provider,
  selectedModel,
  mineItems,
  usableItems,
  mineMode,
  usableMode,
  mineError,
  usableError,
  off,
  mock,
  canSend,
  showPicker,
  emptyCatalog,
  composerDisabled,
  sendDisabled,
  pill,
  accountId,
  load,
  loadMine,
  loadUsable,
  send,
  onModelChange,
  requestNewChat,
  openConversation,
  openConversationMenu,
  closeConversationMenu,
  requestDelete,
  confirmDelete,
  cancelDelete,
  dispose,
} = session;

watch(deleteConfirmOpen, async (open) => {
  if (!open) {
    return;
  }
  await nextTick();
  cancelDeleteButton.value?.focus?.();
});

onMounted(load);
watch(accessReady, (ready) => {
  if (ready) {
    load();
  }
});
onBeforeUnmount(dispose);

function onConversationKeydown(id, event) {
  if (event.key === "ContextMenu" || (event.key === "F10" && event.shiftKey)) {
    event.preventDefault();
    openConversationMenu(id);
  } else if (event.key === "Escape") {
    closeConversationMenu();
  }
}

function onMenuButtonPointerDown(id, event) {
  if (event.pointerType !== "touch") {
    return;
  }
  const timer = setTimeout(() => openConversationMenu(id), 500);
  const clear = () => {
    clearTimeout(timer);
    event.currentTarget?.removeEventListener("pointerup", clear);
    event.currentTarget?.removeEventListener("pointercancel", clear);
  };
  event.currentTarget?.addEventListener("pointerup", clear);
  event.currentTarget?.addEventListener("pointercancel", clear);
}

function onDeleteKeydown(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    cancelDelete();
  }
}

function onKeydown(event) {
  if (composerKeyAction(event) !== "send") {
    return;
  }
  event.preventDefault();
  send();
}

function suggest(text) {
  if (composerDisabled.value) {
    return;
  }
  prompt.value = text;
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
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="agent.qa" />
  <div v-else class="chat-page">
    <PageIntro title="对话" lead="在平台内向已配置的模型提问。" :note="note">
      <template #actions>
        <button type="button" class="btn btn-secondary" @click="requestNewChat">新建对话</button>
        <span v-if="pill" class="pill" :data-provider="provider?.provider">{{ pill }}</span>
      </template>
    </PageIntro>
    <div
      v-if="deleteConfirmOpen"
      class="chat-confirm"
      role="alertdialog"
      aria-labelledby="delete-chat-title"
      aria-describedby="delete-chat-copy"
      @keydown="onDeleteKeydown"
    >
      <h2 id="delete-chat-title">删除对话</h2>
      <p id="delete-chat-copy">{{ DELETE_CHAT_WARNING }}</p>
      <div class="modal-actions">
        <button ref="cancelDeleteButton" type="button" class="btn btn-secondary" @click="cancelDelete">取消</button>
        <button type="button" class="btn" @click="confirmDelete">确认</button>
      </div>
    </div>
    <div class="chat-body">
      <aside class="conversation-rail" aria-label="对话列表">
        <h2>对话</h2>
        <ul>
          <li v-for="item in conversations" :key="item.id" class="conversation-row">
            <button
              type="button"
              class="conversation-item"
              :aria-current="item.id === activeId ? 'true' : undefined"
              @click="openConversation(item.id)"
              @contextmenu.prevent="openConversationMenu(item.id)"
              @keydown="onConversationKeydown(item.id, $event)"
              @pointerdown="onMenuButtonPointerDown(item.id, $event)"
            >
              {{ item.title }}
            </button>
            <button
              type="button"
              class="conversation-more"
              aria-label="更多操作"
              aria-haspopup="menu"
              @click="openConversationMenu(item.id)"
              @pointerdown="onMenuButtonPointerDown(item.id, $event)"
            >
              …
            </button>
            <div v-if="menuConversationId === item.id" class="conversation-menu" role="menu">
              <button type="button" role="menuitem" @click="requestDelete(item.id)">删除</button>
            </div>
          </li>
        </ul>
        <p v-if="listNotice" class="hint" role="alert">{{ listNotice }}</p>
      </aside>
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
                :class="{ user: item.role === 'user', failed: item.failed, interrupted: item.interrupted }"
              >
                <MarkdownBody :text="item.content" />
                <span v-if="item.role === 'assistant' && item.model" class="bubble-model">{{ item.model }}</span>
                <span v-if="item.interrupted" class="fail-mark">{{ INTERRUPTED_MARK }}</span>
                <span v-else-if="item.failed" class="fail-mark">发送失败</span>
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
            <p v-if="interruptNotice" class="hint">{{ interruptNotice }}</p>
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
                  :disabled="sendDisabled || !prompt.trim()"
                >
                  <span v-if="sending" class="spinner" aria-hidden="true"></span>
                  发送
                </button>
              </div>
            </form>
            <p v-if="statusRecovery" class="hint">
              <button type="button" class="btn btn-ghost" @click="load">重试读取模型状态</button>
            </p>
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

/**
 * Chat page lifecycle. ChatView renders this session; tests drive the same
 * object. A late success, error, or finally applies only when the captured
 * chat generation, auth epoch, view, and request ticket are all still current.
 */

import { computed, effectScope, ref, watch } from "vue";

import { accessReady, account, actionDeniedText, perms } from "./access.js";
import { errorText, getJson, isAbortError, isForbidden, postJson } from "./api.js";
import {
  EMPTY_CATALOG_TIP,
  INTERRUPTED_HINT,
  STALE_MODEL_TIP,
  chatModelState,
  chatRequestBody,
  failOutgoingMessage,
  newChatNeedsConfirm,
  readChatModel,
  readChatThreadState,
  shouldRefetchProvider,
  showModelPicker,
  takeUserMessage,
  writeChatModel,
  writeChatThread,
} from "./chat.js";
import {
  advanceChatGeneration,
  currentAuthEpoch,
  currentChatGeneration,
  onIdentityBoundary,
  openChatView,
  registerAbortController,
  scheduleChatPersist,
} from "./identity.js";
import { isUuid } from "./paths.js";
import { canSee, canDo } from "./permissions.js";
import { skillListPath } from "./skillLists.js";

export function createChatSession(options = {}) {
  const storage = options.storage || window.sessionStorage;
  const view = openChatView();
  const loaded = readChatThreadState(storage);
  if (loaded.sawPending) {
    writeChatThread(storage, loaded.messages);
  }

  const messages = ref(loaded.messages);
  const prompt = ref("");
  const error = ref("");
  const pageError = ref("");
  const modelNotice = ref("");
  const interruptNotice = ref(loaded.sawPending ? INTERRUPTED_HINT : "");
  const sending = ref(false);
  const forbidden = ref(false);
  const mode = ref("loading");
  const confirmOpen = ref(false);
  const provider = ref(null);
  const selectedModel = ref(readChatModel(storage));
  const catalogTrusted = ref(false);
  const mineItems = ref([]);
  const usableItems = ref([]);
  const mineMode = ref("idle");
  const usableMode = ref("idle");
  const mineError = ref("");
  const usableError = ref("");

  let selectionRevision = 0;
  let statusSerial = 0;
  let mineSerial = 0;
  let usableSerial = 0;
  let chatSerial = 0;
  let activeChatRequest = 0;

  const scope = effectScope();
  scope.run(() => {
    watch(
      messages,
      () => {
        scheduleChatPersist(
          view.viewToken,
          () => view.isAlive(),
          () => messages.value,
          storage,
        );
      },
      { deep: true, flush: "sync" },
    );
  });

  const stopIdentity = onIdentityBoundary(() => {
    if (!view.isAlive()) {
      return;
    }
    clearPageMemory();
  });

  const off = computed(() => provider.value?.provider === "off");
  const mock = computed(() => provider.value?.provider === "mock");
  const canSend = computed(() => canDo("chat.send", perms.value));
  const showPicker = computed(() => showModelPicker(provider.value));
  const emptyCatalog = computed(
    () => mode.value === "ready" && provider.value?.provider === "openai" && !showPicker.value,
  );
  const canUseSkills = computed(() => canDo("skill.create", perms.value));
  const modelSendBlocked = computed(() => {
    const kind = provider.value?.provider;
    if (kind === "off" || kind === "mock") {
      return false;
    }
    if (!catalogTrusted.value) {
      return true;
    }
    if (kind === "openai" && !showPicker.value) {
      return true;
    }
    if (kind === "openai" && !selectedModel.value) {
      return true;
    }
    return false;
  });
  const composerDisabled = computed(
    () => off.value || !canSend.value || mode.value !== "ready",
  );
  const sendDisabled = computed(
    () => composerDisabled.value || sending.value || modelSendBlocked.value,
  );
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

  function clearPageMemory() {
    messages.value = [];
    prompt.value = "";
    error.value = "";
    pageError.value = "";
    modelNotice.value = "";
    interruptNotice.value = "";
    sending.value = false;
    confirmOpen.value = false;
    selectedModel.value = "";
    provider.value = null;
    catalogTrusted.value = false;
    mode.value = "idle";
    forbidden.value = false;
    mineItems.value = [];
    usableItems.value = [];
    mineError.value = "";
    usableError.value = "";
    mineMode.value = "idle";
    usableMode.value = "idle";
    activeChatRequest = 0;
  }

  function issueTicket(readSerial, bumpSerial) {
    const id = bumpSerial();
    const controller = new AbortController();
    const releaseAbort = registerAbortController(controller);
    const captured = {
      id,
      generation: currentChatGeneration(),
      authEpoch: currentAuthEpoch(),
      viewToken: view.viewToken,
      selectionRevision,
      candidate: selectedModel.value || readChatModel(storage),
    };
    return {
      signal: controller.signal,
      captured,
      current() {
        return (
          readSerial() === id &&
          currentChatGeneration() === captured.generation &&
          currentAuthEpoch() === captured.authEpoch &&
          view.isAlive() &&
          view.viewToken === captured.viewToken
        );
      },
      release() {
        releaseAbort();
      },
    };
  }

  function issueStatusTicket() {
    return issueTicket(
      () => statusSerial,
      () => {
        statusSerial += 1;
        return statusSerial;
      },
    );
  }

  function issueMineTicket() {
    return issueTicket(
      () => mineSerial,
      () => {
        mineSerial += 1;
        return mineSerial;
      },
    );
  }

  function issueUsableTicket() {
    return issueTicket(
      () => usableSerial,
      () => {
        usableSerial += 1;
        return usableSerial;
      },
    );
  }

  function invalidateReadTickets() {
    statusSerial += 1;
    mineSerial += 1;
    usableSerial += 1;
    activeChatRequest = 0;
  }

  function applyProvider(fresh, ticket) {
    if (!ticket.current()) {
      return;
    }
    provider.value = fresh;
    catalogTrusted.value = true;
    mode.value = "ready";
    pageError.value = "";
    const changed = selectionRevision !== ticket.captured.selectionRevision;
    const candidate = changed ? selectedModel.value : ticket.captured.candidate;
    const state = chatModelState(fresh, candidate);
    if (fresh?.provider !== "openai") {
      catalogTrusted.value = true;
      modelNotice.value = "";
      return;
    }
    if (changed && state.showPicker && !state.fellBack) {
      selectedModel.value = state.model;
      modelNotice.value = "";
      if (state.model) {
        writeChatModel(storage, state.model);
      }
      return;
    }
    selectedModel.value = state.model;
    modelNotice.value = state.showPicker && state.fellBack ? STALE_MODEL_TIP : "";
    if (state.model) {
      writeChatModel(storage, state.model);
    }
  }

  async function loadMine() {
    const ticket = issueMineTicket();
    mineMode.value = "loading";
    mineError.value = "";
    try {
      const body = await getJson(skillListPath("mine"), { signal: ticket.signal });
      if (!ticket.current()) {
        return;
      }
      mineItems.value = Array.isArray(body?.items) ? body.items : [];
      mineMode.value = "ready";
    } catch (caught) {
      if (!ticket.current() || isAbortError(caught)) {
        return;
      }
      mineMode.value = "error";
      mineError.value = errorText(caught, "无法读取我产出的 Skill");
    } finally {
      ticket.release();
    }
  }

  async function loadUsable() {
    if (!canUseSkills.value) {
      usableItems.value = [];
      usableMode.value = "hidden";
      usableError.value = "";
      return;
    }
    const ticket = issueUsableTicket();
    usableMode.value = "loading";
    usableError.value = "";
    try {
      const body = await getJson(skillListPath("usable"), { signal: ticket.signal });
      if (!ticket.current()) {
        return;
      }
      usableItems.value = Array.isArray(body?.items) ? body.items : [];
      usableMode.value = "ready";
    } catch (caught) {
      if (!ticket.current() || isAbortError(caught)) {
        return;
      }
      if (isForbidden(caught)) {
        usableItems.value = [];
        usableMode.value = "hidden";
        return;
      }
      usableMode.value = "error";
      usableError.value = errorText(caught, "无法读取已发布 Skill");
    } finally {
      ticket.release();
    }
  }

  function loadSidebar() {
    return Promise.all([loadMine(), loadUsable()]);
  }

  async function load() {
    if (!view.isAlive()) {
      return;
    }
    forbidden.value = false;
    if (accessReady.value && !canSee("chat", perms.value)) {
      forbidden.value = true;
      return;
    }
    const ticket = issueStatusTicket();
    mode.value = "loading";
    pageError.value = "";
    const sidebar = accessReady.value ? loadSidebar() : Promise.resolve();
    try {
      const fresh = await getJson("/api/v1/llm/provider", { signal: ticket.signal });
      if (!ticket.current()) {
        return;
      }
      applyProvider(fresh, ticket);
    } catch (caught) {
      if (!ticket.current() || isAbortError(caught)) {
        return;
      }
      if (isForbidden(caught)) {
        forbidden.value = true;
        return;
      }
      mode.value = "error";
      pageError.value = errorText(caught, "无法读取模型状态");
      catalogTrusted.value = false;
    } finally {
      ticket.release();
    }
    await sidebar;
  }

  async function refetchDisallowedModel(rejected, chatTicket) {
    catalogTrusted.value = false;
    const ticket = issueStatusTicket();
    ticket.captured.candidate = rejected;
    try {
      const fresh = await getJson("/api/v1/llm/provider", { signal: ticket.signal });
      if (!ticket.current() || (chatTicket && !chatTicket.current())) {
        return;
      }
      applyProvider(fresh, ticket);
    } catch (caught) {
      if (!ticket.current() || isAbortError(caught)) {
        return;
      }
    } finally {
      ticket.release();
    }
  }

  function onModelChange() {
    selectionRevision += 1;
    writeChatModel(storage, selectedModel.value);
    modelNotice.value = "";
  }

  function clearTransientOnly() {
    error.value = "";
    pageError.value = "";
    modelNotice.value = "";
    if (mode.value === "error") {
      mode.value = provider.value ? "ready" : "idle";
    }
  }

  function requestNewChat() {
    if (forbidden.value || confirmOpen.value) {
      return;
    }
    const needsConfirm = newChatNeedsConfirm({
      messages: messages.value,
      draft: prompt.value,
      requestActive: sending.value,
    });
    if (!needsConfirm) {
      clearTransientOnly();
      return;
    }
    confirmOpen.value = true;
  }

  function cancelNewChat() {
    confirmOpen.value = false;
  }

  function confirmNewChat() {
    if (!confirmOpen.value) {
      return;
    }
    confirmOpen.value = false;
    applyNewChat();
  }

  function applyNewChat() {
    advanceChatGeneration();
    invalidateReadTickets();
    sending.value = false;
    messages.value = [];
    prompt.value = "";
    error.value = "";
    pageError.value = "";
    modelNotice.value = "";
    interruptNotice.value = "";
    writeChatThread(storage, []);
    if (catalogTrusted.value && provider.value) {
      if (provider.value.provider === "openai") {
        const candidate = selectedModel.value || readChatModel(storage);
        const state = chatModelState(provider.value, candidate);
        selectedModel.value = state.model;
        modelNotice.value = state.showPicker && state.fellBack ? STALE_MODEL_TIP : "";
        if (state.model) {
          writeChatModel(storage, state.model);
        }
      } else {
        modelNotice.value = "";
      }
      if (accessReady.value && canSee("chat", perms.value)) {
        loadSidebar();
      }
      return;
    }
    const candidate = selectedModel.value || readChatModel(storage);
    selectedModel.value = candidate;
    catalogTrusted.value = false;
    load();
  }

  function beginChatRequest() {
    const controller = new AbortController();
    const releaseAbort = registerAbortController(controller);
    chatSerial += 1;
    const requestId = chatSerial;
    activeChatRequest = requestId;
    const captured = {
      requestId,
      generation: currentChatGeneration(),
      authEpoch: currentAuthEpoch(),
      viewToken: view.viewToken,
    };
    return {
      signal: controller.signal,
      current() {
        return (
          activeChatRequest === requestId &&
          currentChatGeneration() === captured.generation &&
          currentAuthEpoch() === captured.authEpoch &&
          view.isAlive() &&
          view.viewToken === captured.viewToken
        );
      },
      release() {
        releaseAbort();
      },
    };
  }

  async function send() {
    if (!view.isAlive() || sending.value || composerDisabled.value || modelSendBlocked.value) {
      return;
    }
    const started = takeUserMessage(prompt.value, messages.value);
    if (!started) {
      return;
    }
    const outboundModel = showPicker.value ? selectedModel.value : "";
    const payload = chatRequestBody(messages.value, started.content, outboundModel);
    const pendingMessages = started.messages.map((item, index, list) => {
      if (index !== list.length - 1) {
        return item;
      }
      return { ...item, pending: true };
    });
    const ticket = beginChatRequest();
    sending.value = true;
    error.value = "";
    messages.value = pendingMessages;
    prompt.value = started.prompt;
    writeChatThread(storage, messages.value);
    if (options.afterTurn) {
      await options.afterTurn();
    }
    try {
      const body = await postJson("/api/v1/llm/chat", payload, { signal: ticket.signal });
      if (!ticket.current()) {
        return;
      }
      const actions = Array.isArray(body?.actions)
        ? body.actions.filter((item) => actionTarget(item))
        : [];
      const reply = { role: "assistant", content: body?.content || "", actions };
      if (typeof body?.model === "string" && body.model.length > 0) {
        reply.model = body.model;
      }
      const visible = messages.value.map((item) => {
        if (!item?.pending) {
          return item;
        }
        const next = { ...item };
        delete next.pending;
        return next;
      });
      messages.value = [...visible, reply];
      writeChatThread(storage, messages.value);
      if (actions.some((item) => item.type === "skill_drafted" || item.type === "skill_updated")) {
        loadSidebar();
      }
      if (options.afterTurn) {
        await options.afterTurn();
      }
    } catch (caught) {
      if (!ticket.current() || isAbortError(caught)) {
        return;
      }
      const failed = failOutgoingMessage(messages.value, prompt.value);
      messages.value = failed.messages;
      prompt.value = failed.prompt;
      writeChatThread(storage, messages.value);
      if (shouldRefetchProvider(caught)) {
        await refetchDisallowedModel(outboundModel, ticket);
      }
      if (!ticket.current()) {
        return;
      }
      error.value = isForbidden(caught)
        ? actionDeniedText("agent.qa")
        : errorText(caught, "发送失败");
    } finally {
      ticket.release();
      if (ticket.current()) {
        sending.value = false;
      }
    }
  }

  function dispose() {
    stopIdentity();
    view.close();
    scope.stop();
  }

  return {
    messages,
    prompt,
    error,
    pageError,
    modelNotice,
    interruptNotice,
    sending,
    forbidden,
    mode,
    confirmOpen,
    provider,
    selectedModel,
    catalogTrusted,
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
    canUseSkills,
    modelSendBlocked,
    composerDisabled,
    sendDisabled,
    pill,
    accountId: computed(() => account.value?.id || ""),
    load,
    loadMine,
    loadUsable,
    send,
    onModelChange,
    requestNewChat,
    cancelNewChat,
    confirmNewChat,
    dispose,
    emptyCatalogTip: EMPTY_CATALOG_TIP,
  };
}

function actionTarget(action) {
  if (!action || !isUuid(action.id)) {
    return null;
  }
  if (action.type === "skill_drafted" || action.type === "skill_updated" || action.type === "build_started") {
    return action;
  }
  return null;
}

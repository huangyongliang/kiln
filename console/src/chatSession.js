/**
 * Chat page lifecycle. ChatView renders this session; tests drive the same
 * object. A late success, error, or finally writes only to the conversation
 * that started it, and only while that request's auth epoch and serial are
 * still current. Switching or creating a conversation does not abort it.
 */

import { computed, effectScope, ref, watch } from "vue";

import { accessReady, account, actionDeniedText, perms } from "./access.js";
import { errorText, getJson, isAbortError, isForbidden, postJson } from "./api.js";
import {
  CHAT_CONVERSATION_LIMIT,
  CHAT_CONVERSATIONS_KEY,
  CONVERSATION_LIMIT_TIP,
  CONVERSATION_SAVE_TIP,
  EMPTY_CATALOG_TIP,
  INTERRUPTED_HINT,
  STALE_MODEL_TIP,
  chatModelState,
  chatRequestBody,
  conversationTitleFromContent,
  conversationsFromSnapshot,
  createConversationRecord,
  failOutgoingMessage,
  migrateChatStorage,
  newChatNeedsConfirm,
  readActiveConversationId,
  readChatModel,
  shouldRefetchProvider,
  showModelPicker,
  takeUserMessage,
  writeActiveConversationId,
  writeChatModel,
  writeConversationStore,
} from "./chat.js";
import {
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
  const localStore = options.localStorage || window.localStorage;
  const sessionStore = options.sessionStorage || window.sessionStorage;
  const view = openChatView();
  const migrated = migrateChatStorage(localStore, sessionStore);

  const records = ref(withoutRuntime(migrated.conversations));
  const messages = ref([]);
  const prompt = ref("");
  const error = ref("");
  const pageError = ref("");
  const modelNotice = ref("");
  const interruptNotice = ref("");
  const sending = ref(false);
  const forbidden = ref(false);
  const mode = ref("loading");
  const statusRecovery = ref(false);
  const confirmOpen = ref(false);
  const provider = ref(null);
  const selectedModel = ref(readChatModel(localStore) || readChatModel(sessionStore));
  const catalogTrusted = ref(false);
  const mineItems = ref([]);
  const usableItems = ref([]);
  const mineMode = ref("idle");
  const usableMode = ref("idle");
  const mineError = ref("");
  const usableError = ref("");
  const activeId = ref("");
  const listNotice = ref(migrated.saveFailed ? CONVERSATION_SAVE_TIP : "");
  const menuConversationId = ref("");
  const deleteConfirmOpen = ref(false);
  const deleteTargetId = ref("");

  let selectionRevision = 0;
  let statusSerial = 0;
  let mineSerial = 0;
  let usableSerial = 0;
  let lastStamp = 0;
  let applying = false;
  const inflight = new Map();
  const serials = new Map();
  const errors = new Map();

  if (records.value.length === 0) {
    records.value = [createConversationRecord({ now: stamp() })];
  }
  showConversation(pickConversation(records.value, readActiveConversationId(sessionStore)));
  persistNow();

  const scope = effectScope();
  scope.run(() => {
    watch(
      [messages, prompt],
      () => {
        if (applying || !view.isAlive()) {
          return;
        }
        syncActive();
        schedulePersist();
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

  if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
    window.addEventListener("storage", onStorage);
  }

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
  const conversationList = computed(() => [...records.value].sort(byRecent));
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

  function stamp() {
    const now = Date.now();
    lastStamp = now > lastStamp ? now : lastStamp + 1;
    return lastStamp;
  }

  function find(id) {
    return records.value.find((item) => item.id === id) || null;
  }

  function rememberModel(model) {
    writeChatModel(localStore, model);
    if (sessionStore && sessionStore !== localStore) {
      try {
        sessionStore.removeItem("kiln.chat.model");
      } catch {
        // localStorage remains the model the next tab reads.
      }
    }
  }

  function syncActive() {
    const conversation = find(activeId.value);
    if (!conversation) {
      return null;
    }
    const nextMessages = messages.value.map(cloneMessage);
    const nextDraft = prompt.value;
    const changed =
      conversation.draft !== nextDraft || messageSignature(conversation.messages) !== messageSignature(nextMessages);
    conversation.messages = nextMessages;
    conversation.draft = nextDraft;
    if (changed) {
      conversation.updatedAt = stamp();
    }
    return conversation;
  }

  function showConversation(conversation) {
    if (!conversation) {
      return;
    }
    applying = true;
    activeId.value = conversation.id;
    writeActiveConversationId(sessionStore, conversation.id);
    messages.value = conversation.messages.map(cloneMessage);
    prompt.value = conversation.draft || "";
    sending.value = inflight.has(conversation.id);
    error.value = errors.get(conversation.id) || "";
    interruptNotice.value = messages.value.some((item) => item.interrupted) ? INTERRUPTED_HINT : "";
    applying = false;
  }

  function persistNow() {
    const saved = writeConversationStore(localStore, records.value);
    if (!saved) {
      listNotice.value = CONVERSATION_SAVE_TIP;
      return false;
    }
    if (listNotice.value === CONVERSATION_SAVE_TIP) {
      listNotice.value = "";
    }
    return true;
  }

  function schedulePersist() {
    scheduleChatPersist(
      view.viewToken,
      () => view.isAlive(),
      () => records.value.map(cloneConversation),
      localStore,
      (storage, snapshot) => {
        const saved = writeConversationStore(storage, snapshot);
        if (!saved && view.isAlive()) {
          listNotice.value = CONVERSATION_SAVE_TIP;
        }
      },
    );
  }

  function clearPageMemory() {
    applying = true;
    for (const entry of inflight.values()) {
      try {
        entry.controller.abort();
      } catch {
        // Identity teardown already aborts the same controllers.
      }
    }
    inflight.clear();
    errors.clear();
    records.value = [];
    activeId.value = "";
    messages.value = [];
    prompt.value = "";
    error.value = "";
    pageError.value = "";
    modelNotice.value = "";
    interruptNotice.value = "";
    sending.value = false;
    confirmOpen.value = false;
    deleteConfirmOpen.value = false;
    menuConversationId.value = "";
    deleteTargetId.value = "";
    listNotice.value = "";
    statusRecovery.value = false;
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
    applying = false;
    const epoch = currentAuthEpoch();
    queueMicrotask(() => {
      if (!view.isAlive() || currentAuthEpoch() !== epoch || records.value.length > 0) {
        return;
      }
      const created = createConversationRecord({ now: stamp() });
      records.value = [created];
      showConversation(created);
      schedulePersist();
    });
  }

  function issueTicket(readSerial, bumpSerial) {
    const id = bumpSerial();
    const controller = new AbortController();
    const releaseAbort = registerAbortController(controller, () => readSerial() === id);
    const captured = {
      id,
      generation: currentChatGeneration(),
      authEpoch: currentAuthEpoch(),
      viewToken: view.viewToken,
      selectionRevision,
      candidate: selectedModel.value || readChatModel(localStore) || readChatModel(sessionStore),
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
        rememberModel(state.model);
      }
      return;
    }
    selectedModel.value = state.model;
    modelNotice.value = state.showPicker && state.fellBack ? STALE_MODEL_TIP : "";
    if (state.model) {
      rememberModel(state.model);
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
    statusRecovery.value = false;
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
      if (!ticket.current() || (chatTicket && !chatTicket.canWrite())) {
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
    rememberModel(selectedModel.value);
    modelNotice.value = "";
  }

  function clearTransientOnly() {
    error.value = "";
    pageError.value = "";
    modelNotice.value = "";
    errors.delete(activeId.value);
    if (mode.value !== "error") {
      return;
    }
    if (provider.value && catalogTrusted.value) {
      mode.value = "ready";
      return;
    }
    mode.value = "idle";
    statusRecovery.value = true;
  }

  function closeConversationMenu() {
    menuConversationId.value = "";
  }

  function openConversationMenu(id) {
    if (!find(id)) {
      return;
    }
    menuConversationId.value = id;
  }

  function openConversation(id) {
    if (!id || id === activeId.value || deleteConfirmOpen.value) {
      return;
    }
    const next = find(id);
    if (!next) {
      return;
    }
    syncActive();
    persistNow();
    showConversation(next);
    closeConversationMenu();
  }

  function requestNewChat() {
    if (forbidden.value || deleteConfirmOpen.value) {
      return;
    }
    closeConversationMenu();
    syncActive();
    const conversation = find(activeId.value);
    const idle =
      conversation &&
      !newChatNeedsConfirm({
        messages: conversation.messages,
        draft: conversation.draft,
        requestActive: inflight.has(conversation.id),
      });
    if (idle) {
      clearTransientOnly();
      return;
    }
    if (records.value.length >= CHAT_CONVERSATION_LIMIT) {
      listNotice.value = CONVERSATION_LIMIT_TIP;
      return;
    }
    const created = createConversationRecord({ now: stamp() });
    records.value = [...records.value, created];
    showConversation(created);
    if (listNotice.value === CONVERSATION_LIMIT_TIP) {
      listNotice.value = "";
    }
    persistNow();
    settleNewConversation();
  }

  function settleNewConversation() {
    invalidateReadTickets();
    error.value = "";
    pageError.value = "";
    modelNotice.value = "";
    interruptNotice.value = "";
    errors.delete(activeId.value);
    if (catalogTrusted.value && provider.value) {
      mode.value = "ready";
      pageError.value = "";
      if (provider.value.provider === "openai") {
        const candidate = selectedModel.value || readChatModel(localStore);
        const state = chatModelState(provider.value, candidate);
        selectedModel.value = state.model;
        modelNotice.value = state.showPicker && state.fellBack ? STALE_MODEL_TIP : "";
        if (state.model) {
          rememberModel(state.model);
        }
      } else {
        modelNotice.value = "";
      }
      if (accessReady.value && canSee("chat", perms.value)) {
        loadSidebar();
      }
      return;
    }
    const candidate = selectedModel.value || readChatModel(localStore);
    selectedModel.value = candidate;
    catalogTrusted.value = false;
    load();
  }

  function requestDelete(id) {
    const target = id || menuConversationId.value;
    if (!find(target)) {
      return;
    }
    deleteTargetId.value = target;
    menuConversationId.value = "";
    deleteConfirmOpen.value = true;
  }

  function cancelDelete() {
    deleteConfirmOpen.value = false;
    deleteTargetId.value = "";
    menuConversationId.value = "";
  }

  function confirmDelete() {
    const id = deleteTargetId.value;
    if (!id || !deleteConfirmOpen.value) {
      return;
    }
    deleteConfirmOpen.value = false;
    deleteTargetId.value = "";
    menuConversationId.value = "";
    const pending = inflight.get(id);
    if (pending) {
      inflight.delete(id);
      try {
        pending.controller.abort();
      } catch {
        // The conversation is already leaving the list.
      }
    }
    errors.delete(id);
    const remaining = records.value.filter((item) => item.id !== id);
    if (remaining.length === 0) {
      const created = createConversationRecord({ now: stamp() });
      records.value = [created];
      showConversation(created);
    } else {
      records.value = remaining;
      if (activeId.value === id) {
        showConversation(pickConversation(remaining, ""));
      }
    }
    persistNow();
  }

  function beginChatRequest(conversationId) {
    const previous = inflight.get(conversationId);
    if (previous) {
      try {
        previous.controller.abort();
      } catch {
        // The newer send on this conversation replaces it.
      }
    }
    const controller = new AbortController();
    const requestSerial = (serials.get(conversationId) || 0) + 1;
    serials.set(conversationId, requestSerial);
    const authEpoch = currentAuthEpoch();
    const entry = { serial: requestSerial, controller, authEpoch };
    inflight.set(conversationId, entry);
    const releaseAbort = registerAbortController(controller, () => {
      const current = inflight.get(conversationId);
      return Boolean(
        current &&
          current.serial === requestSerial &&
          activeId.value === conversationId &&
          view.isAlive(),
      );
    });
    return {
      signal: controller.signal,
      conversationId,
      requestSerial,
      canWrite() {
        const current = inflight.get(conversationId);
        return Boolean(
          current &&
            current.serial === requestSerial &&
            currentAuthEpoch() === authEpoch &&
            view.isAlive(),
        );
      },
      release() {
        releaseAbort();
      },
    };
  }

  function commitConversation(conversationId, nextMessages) {
    const conversation = find(conversationId);
    if (!conversation) {
      return;
    }
    conversation.messages = nextMessages.map(cloneMessage);
    conversation.updatedAt = stamp();
    if (activeId.value === conversationId) {
      applying = true;
      messages.value = conversation.messages.map(cloneMessage);
      applying = false;
    }
    persistNow();
  }

  function settleSend(ticket) {
    const current = inflight.get(ticket.conversationId);
    if (current && current.serial === ticket.requestSerial && !ticket.signal.aborted) {
      inflight.delete(ticket.conversationId);
    }
    if (activeId.value === ticket.conversationId) {
      sending.value = inflight.has(ticket.conversationId);
    }
  }

  async function send() {
    if (!view.isAlive() || sending.value || composerDisabled.value || modelSendBlocked.value) {
      return;
    }
    const conversation = syncActive();
    if (!conversation) {
      return;
    }
    const started = takeUserMessage(prompt.value, messages.value);
    if (!started) {
      return;
    }
    if (!conversation.titleLocked) {
      conversation.title = conversationTitleFromContent(started.content);
      conversation.titleLocked = true;
      conversation.updatedAt = stamp();
    }
    const outboundModel = showPicker.value ? selectedModel.value : "";
    const payload = chatRequestBody(messages.value, started.content, outboundModel);
    const pendingMessages = started.messages.map((item, index, list) => {
      if (index !== list.length - 1) {
        return item;
      }
      return { ...item, pending: true };
    });
    const ticket = beginChatRequest(conversation.id);
    sending.value = true;
    error.value = "";
    errors.delete(conversation.id);
    messages.value = pendingMessages;
    prompt.value = started.prompt;
    persistNow();
    if (options.afterTurn) {
      await options.afterTurn();
    }
    try {
      const body = await postJson("/api/v1/llm/chat", payload, { signal: ticket.signal });
      if (!ticket.canWrite()) {
        return;
      }
      const actions = Array.isArray(body?.actions)
        ? body.actions.filter((item) => actionTarget(item))
        : [];
      const reply = { role: "assistant", content: body?.content || "", actions };
      if (typeof body?.model === "string" && body.model.length > 0) {
        reply.model = body.model;
      }
      const source =
        activeId.value === ticket.conversationId
          ? messages.value
          : find(ticket.conversationId)?.messages || [];
      const visible = source.map((item) => {
        if (!item?.pending) {
          return item;
        }
        const next = { ...item };
        delete next.pending;
        return next;
      });
      commitConversation(ticket.conversationId, [...visible, reply]);
      if (
        activeId.value === ticket.conversationId &&
        actions.some((item) => item.type === "skill_drafted" || item.type === "skill_updated")
      ) {
        loadSidebar();
      }
      if (options.afterTurn) {
        await options.afterTurn();
      }
    } catch (caught) {
      if (!ticket.canWrite() || isAbortError(caught)) {
        return;
      }
      const current = find(ticket.conversationId);
      const liveMessages = activeId.value === ticket.conversationId ? messages.value : current?.messages || [];
      const liveDraft = activeId.value === ticket.conversationId ? prompt.value : current?.draft || "";
      const failed = failOutgoingMessage(liveMessages, liveDraft);
      commitConversation(ticket.conversationId, failed.messages);
      if (activeId.value === ticket.conversationId) {
        prompt.value = failed.prompt;
      } else if (current) {
        current.draft = failed.prompt;
      }
      if (activeId.value === ticket.conversationId && shouldRefetchProvider(caught)) {
        await refetchDisallowedModel(outboundModel, ticket);
      }
      if (!ticket.canWrite()) {
        return;
      }
      const message = isForbidden(caught) ? actionDeniedText("agent.qa") : errorText(caught, "发送失败");
      errors.set(ticket.conversationId, message);
      if (activeId.value === ticket.conversationId) {
        error.value = message;
      }
    } finally {
      ticket.release();
      if (ticket.canWrite()) {
        settleSend(ticket);
      }
    }
  }

  function onStorage(event) {
    if (!view.isAlive() || !event || event.key !== CHAT_CONVERSATIONS_KEY) {
      return;
    }
    adoptExternal(event.newValue);
  }

  function adoptExternal(raw) {
    if (raw == null) {
      for (const entry of inflight.values()) {
        try {
          entry.controller.abort();
        } catch {
          // A cleared store has nothing left to complete into.
        }
      }
      inflight.clear();
      const created = createConversationRecord({ now: stamp() });
      records.value = [created];
      showConversation(created);
      persistNow();
      return;
    }
    const incoming = conversationsFromSnapshot(raw);
    if (!incoming) {
      return;
    }
    const incomingIds = new Set(incoming.map((item) => item.id));
    const kept = new Set();
    const merged = [];
    let protectLocal = false;
    for (const item of incoming) {
      const local = find(item.id);
      if (local && keepLocal(local, item)) {
        merged.push(local);
        kept.add(local.id);
        protectLocal = true;
      } else {
        merged.push(withoutRuntime([item])[0]);
      }
    }
    for (const local of records.value) {
      if (incomingIds.has(local.id)) {
        continue;
      }
      const pending = inflight.get(local.id);
      if (pending) {
        inflight.delete(local.id);
        try {
          pending.controller.abort();
        } catch {
          // The other tab already removed this conversation.
        }
      }
      errors.delete(local.id);
    }
    records.value = merged;
    if (!find(activeId.value)) {
      if (records.value.length === 0) {
        const created = createConversationRecord({ now: stamp() });
        records.value = [created];
        showConversation(created);
        protectLocal = true;
      } else {
        showConversation(pickConversation(records.value, ""));
      }
    } else if (!kept.has(activeId.value)) {
      showConversation(find(activeId.value));
    }
    if (protectLocal) {
      persistNow();
    }
  }

  function keepLocal(local, incoming) {
    if (inflight.has(local.id)) {
      return true;
    }
    return Number(incoming?.updatedAt || 0) < Number(local.updatedAt || 0);
  }

  function dispose() {
    stopIdentity();
    if (typeof window !== "undefined" && typeof window.removeEventListener === "function") {
      window.removeEventListener("storage", onStorage);
    }
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
    statusRecovery,
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
    conversations: conversationList,
    activeId,
    listNotice,
    menuConversationId,
    deleteConfirmOpen,
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
    openConversation,
    openConversationMenu,
    closeConversationMenu,
    requestDelete,
    confirmDelete,
    cancelDelete,
    dispose,
    emptyCatalogTip: EMPTY_CATALOG_TIP,
  };
}

function confirmNewChat() {}

function cancelNewChat() {}

function withoutRuntime(conversations) {
  return (conversations || []).map((item) => {
    const copy = { ...item, messages: (item.messages || []).map(cloneMessage) };
    delete copy.sawPending;
    return copy;
  });
}

function cloneConversation(item) {
  return {
    ...item,
    messages: (item.messages || []).map(cloneMessage),
  };
}

function cloneMessage(item) {
  const copy = { ...item };
  if (Array.isArray(item?.actions)) {
    copy.actions = item.actions.map((action) => ({ ...action }));
  }
  return copy;
}

function messageSignature(messages) {
  return JSON.stringify((messages || []).map(cloneMessage));
}

function byRecent(left, right) {
  return (
    Number(right.updatedAt || 0) - Number(left.updatedAt || 0) ||
    Number(right.createdAt || 0) - Number(left.createdAt || 0) ||
    String(right.id).localeCompare(String(left.id))
  );
}

function pickConversation(list, id) {
  const found = list.find((item) => item.id === id);
  if (found) {
    return found;
  }
  return [...list].sort(byRecent)[0] || null;
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

<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";

import { accessReady, account, actionDeniedText, perms } from "../access";
import { BUILD_OWNED_REASON, actorLabel, reservationBody, uploadAllowed } from "../actors";
import { errorText, getJson, isForbidden, postJson, putBytes } from "../api";
import AppIcon from "../components/AppIcon.vue";
import CopyButton from "../components/CopyButton.vue";
import FormDialog from "../components/FormDialog.vue";
import PageIntro from "../components/PageIntro.vue";
import StatePanel from "../components/StatePanel.vue";
import StatusTag from "../components/StatusTag.vue";
import { pageCrumb } from "../crumb";
import { formatBytes, formatTime, shortDigest, sourceLabel } from "../format";
import { checksumError, fieldErrors, uploadSizeError, usernameError, versionError } from "../forms";
import { apiPath, isUuid } from "../paths";
import { canDo, canSee } from "../permissions";

const route = useRoute();
const artifact = ref(null);
const accounts = ref(null);
const mode = ref("loading");
const error = ref("");
const forbidden = ref(false);
const missing = ref(false);
const notice = ref("");
const box = reactive({
  open: false,
  kind: "",
  busy: false,
  error: "",
  errors: {},
  version: "",
  file: null,
  checksum: "",
  target: null,
  requester: "",
  ttl: 900,
  reservation: null,
});

const canUpload = computed(() => canDo("artifact.upload", perms.value));
const uploadOffered = computed(() => uploadAllowed(artifact.value, canUpload.value));
const canReserve = computed(() => canDo("artifact.reserve", perms.value));
const canHash = computed(() => window.isSecureContext && Boolean(window.crypto?.subtle));
const requesterLocked = computed(() => Boolean(account.value?.username));
const downloadHref = computed(() => {
  const path = box.reservation?.retrieve_path || "";
  return account.value && path.startsWith("/api/v1/retrieve/") ? `${path}/content` : "";
});

onMounted(load);
watch(() => route.params.id, load);
watch(accessReady, (ready) => {
  if (ready) load();
});

async function loadAccounts() {
  try {
    const body = await getJson("/api/v1/accounts");
    accounts.value = Array.isArray(body?.items) ? body.items : null;
  } catch {
    accounts.value = null;
  }
}

async function load() {
  forbidden.value = false;
  missing.value = false;
  const id = route.params.id;
  if (!isUuid(id)) {
    artifact.value = null;
    missing.value = true;
    mode.value = "ready";
    pageCrumb.value = "制品";
    return;
  }
  if (accessReady.value && !canSee("artifact", perms.value)) {
    forbidden.value = true;
    return;
  }
  mode.value = "loading";
  error.value = "";
  await loadAccounts();
  try {
    artifact.value = await getJson(apiPath`/api/v1/artifacts/${id}`);
    pageCrumb.value = `制品 / ${artifact.value.name}`;
    mode.value = "ready";
  } catch (caught) {
    if (isForbidden(caught)) {
      forbidden.value = true;
      return;
    }
    mode.value = "error";
    error.value = errorText(caught, "无法读取制品");
  }
}

function resetBox(kind, target = null) {
  Object.assign(box, {
    open: true,
    kind,
    busy: false,
    error: "",
    errors: {},
    version: "",
    file: null,
    checksum: "",
    target,
    requester: account.value?.username || "",
    ttl: 900,
    reservation: null,
  });
}

async function digestHex(file, algorithm) {
  const name = algorithm === "sha512" ? "SHA-512" : "SHA-256";
  const digest = await crypto.subtle.digest(name, await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function chosenChecksum(algorithm) {
  const errors = {};
  const sizeMessage = uploadSizeError(box.file.size);
  if (sizeMessage) errors.file = sizeMessage;
  if (Object.keys(errors).length) return { errors, checksum: "" };
  if (canHash.value) {
    try {
      return { errors, checksum: await digestHex(box.file, algorithm) };
    } catch {
      return { errors: { checksum: "无法在浏览器里计算校验和" }, checksum: "" };
    }
  }
  const pastedMessage = checksumError(box.checksum, algorithm);
  return pastedMessage
    ? { errors: { checksum: pastedMessage }, checksum: "" }
    : { errors, checksum: box.checksum.trim().toLowerCase() };
}

async function submitBytes() {
  const errors = {};
  if (box.kind === "upload") {
    const versionMessage = versionError(box.version.trim());
    if (versionMessage) errors.version = versionMessage;
  }
  if (!box.file) errors.file = "请选择文件";
  box.errors = errors;
  box.error = "";
  if (Object.keys(errors).length) return;
  const algorithm = box.kind === "fill" ? box.target?.checksum?.algorithm || "sha256" : "sha256";
  box.busy = true;
  try {
    const picked = await chosenChecksum(algorithm);
    box.errors = picked.errors;
    if (!picked.checksum) return;
    if (box.kind === "fill" && picked.checksum !== String(box.target.checksum?.value || "").toLowerCase()) {
      box.error = "校验和与登记值不一致";
      return;
    }
    const version = box.kind === "fill" ? box.target.version : box.version.trim();
    const id = route.params.id;
    const result = await putBytes(
      apiPath`/api/v1/artifacts/${id}/versions/${version}/content`,
      box.file,
      {
        "X-Kiln-Checksum-Algorithm": algorithm,
        "X-Kiln-Checksum": picked.checksum,
        "X-Kiln-Source": "upload",
      },
    );
    notice.value = result.status === 201 ? "已上传" : "相同字节已保存";
    box.open = false;
    await load();
  } catch (caught) {
    if (isForbidden(caught)) {
      box.error = actionDeniedText("artifact.upload");
      return;
    }
    const mapped = fieldErrors(caught?.detail);
    if (Object.keys(mapped).length) {
      box.errors = mapped;
      return;
    }
    box.error = errorText(caught, box.kind === "fill" ? "无法补传字节" : "无法上传");
  } finally {
    box.busy = false;
  }
}

async function submitReserve() {
  if (box.reservation) return;
  const body = reservationBody(account.value, box.requester, box.ttl);
  if (!account.value?.username) {
    const whoMessage = usernameError(body.requester || "");
    box.errors = whoMessage ? { requester: whoMessage } : {};
    box.error = "";
    if (whoMessage) return;
  } else {
    box.errors = {};
    box.error = "";
  }
  box.busy = true;
  try {
    const id = route.params.id;
    const version = box.target.version;
    box.reservation = await postJson(
      apiPath`/api/v1/artifacts/${id}/versions/${version}/download-reservations`,
      body,
    );
  } catch (caught) {
    if (isForbidden(caught)) {
      box.error = actionDeniedText("artifact.download");
      return;
    }
    const mapped = fieldErrors(caught?.detail);
    if (Object.keys(mapped).length) {
      box.errors = mapped;
      return;
    }
    box.error = errorText(caught, "无法预约下载");
  } finally {
    box.busy = false;
  }
}

function submit() {
  if (box.busy) return;
  if (box.kind === "reserve") submitReserve();
  else submitBytes();
}
</script>

<template>
  <StatePanel v-if="forbidden" forbidden forbidden-code="artifact.read" />
  <template v-else>
    <PageIntro :title="artifact?.name || '制品'" lead="版本入库之后不可修改。">
      <template v-if="artifact" #meta>
        <p class="meta-row">
          <span class="tag draft">{{ artifact.package_type_label }}</span>
          <span v-if="artifact.group" class="muted">{{ artifact.group }}</span>
        </p>
      </template>
      <template v-if="artifact && uploadOffered" #actions>
        <button type="button" class="btn" @click="resetBox('upload')">上传新版本</button>
      </template>
    </PageIntro>
    <p v-if="artifact?.build_owned" class="hint">{{ BUILD_OWNED_REASON }}</p>
    <p v-if="artifact?.description" class="prose">{{ artifact.description }}</p>
    <p v-if="notice" class="message">{{ notice }}</p>
    <StatePanel
      :loading="mode === 'loading'"
      :error="mode === 'error' ? error : ''"
      :empty="missing"
      empty-text="不存在"
      @retry="load"
    >
      <section v-if="artifact" class="panel">
        <div class="panel-head">
          <h2>版本</h2>
          <p class="lock-note"><AppIcon name="lock" :size="16" />版本不可变</p>
        </div>
        <StatePanel :empty="artifact.versions.length === 0" empty-text="尚无版本">
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>版本</th><th>校验</th><th>大小</th><th>来源</th><th>字节</th><th>操作人</th><th>入库时间</th><th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in artifact.versions" :key="item.version">
                  <td class="mono nowrap">{{ item.version }}</td>
                  <td class="nowrap">
                    <span class="mono">{{ item.checksum.algorithm }} {{ shortDigest(item.checksum.value) }}</span>
                    <CopyButton :value="item.checksum.value" />
                  </td>
                  <td class="nowrap">{{ formatBytes(item.size_bytes) }}</td>
                  <td>{{ sourceLabel(item.source) }}</td>
                  <td class="nowrap">
                    <StatusTag
                      :status="item.content_available ? 'success' : 'unknown'"
                      :label="item.content_available ? '已入库' : '尚无字节'"
                    />
                  </td>
                  <td>{{ actorLabel(item.created_by, accounts) }}</td>
                  <td class="nowrap">{{ formatTime(item.created_at) }}</td>
                  <td class="nowrap">
                    <button
                      v-if="!item.content_available && uploadOffered"
                      type="button"
                      class="btn btn-secondary"
                      @click="resetBox('fill', item)"
                    >
                      补传字节
                    </button>
                    <button
                      v-if="item.content_available && canReserve"
                      type="button"
                      class="btn btn-secondary"
                      @click="resetBox('reserve', item)"
                    >
                      预约下载
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </StatePanel>
      </section>
    </StatePanel>
    <FormDialog
      v-if="box.open"
      :title="box.kind === 'upload' ? '上传新版本' : box.kind === 'fill' ? '补传字节' : '预约下载'"
      :busy="box.busy"
      :error="box.error"
      :done="Boolean(box.reservation)"
      @close="box.open = false"
      @submit="submit"
    >
      <template v-if="box.reservation">
        <dl class="kv">
          <dt>预约</dt>
          <dd class="mono">{{ box.reservation.id }}</dd>
          <dt>预约账号</dt>
          <dd>{{ actorLabel(box.reservation.account_id, accounts) }}</dd>
          <dt>到期</dt>
          <dd>{{ formatTime(box.reservation.expires_at) }}</dd>
          <dt>路径</dt>
          <dd>
            <span class="mono">{{ box.reservation.retrieve_path }}</span>
            <CopyButton :value="box.reservation.retrieve_path" />
          </dd>
        </dl>
        <p v-if="downloadHref" class="reserve-download"><a class="btn btn-secondary" :href="downloadHref">下载</a></p>
      </template>
      <template v-else-if="box.kind === 'reserve'">
        <div class="field">
          <label for="reserve-requester">取用人</label>
          <input id="reserve-requester" v-model="box.requester" type="text" autocomplete="off" :readonly="requesterLocked" />
          <p v-if="box.errors.requester" class="field-error">{{ box.errors.requester }}</p>
        </div>
        <div class="field">
          <label for="reserve-ttl">有效期</label>
          <select id="reserve-ttl" v-model.number="box.ttl">
            <option :value="900">15 分钟</option>
            <option :value="3600">1 小时</option>
            <option :value="86400">24 小时</option>
          </select>
        </div>
      </template>
      <template v-else>
        <p v-if="box.kind === 'fill'" class="hint dialog-hint">
          版本 {{ box.target?.version }} · {{ box.target?.checksum?.algorithm }}
        </p>
        <div v-if="box.kind === 'upload'" class="field">
          <label for="upload-version">版本</label>
          <input id="upload-version" v-model="box.version" type="text" autocomplete="off" />
          <p v-if="box.errors.version" class="field-error">{{ box.errors.version }}</p>
        </div>
        <div class="field">
          <label for="byte-file">文件</label>
          <input id="byte-file" type="file" @change="box.file = $event.target.files?.[0] || null" />
          <p v-if="box.errors.file" class="field-error">{{ box.errors.file }}</p>
        </div>
        <div v-if="!canHash" class="field">
          <label for="byte-checksum">{{ box.kind === "fill" ? box.target?.checksum?.algorithm || "sha256" : "sha256" }} 校验和</label>
          <input id="byte-checksum" v-model="box.checksum" type="text" autocomplete="off" spellcheck="false" />
          <p class="hint">
            {{ box.target?.checksum?.algorithm === "sha512" ? "在本机计算校验和后粘贴" : "在本机运行 sha256sum 后粘贴" }}
          </p>
          <p v-if="box.errors.checksum" class="field-error">{{ box.errors.checksum }}</p>
        </div>
      </template>
    </FormDialog>
  </template>
</template>

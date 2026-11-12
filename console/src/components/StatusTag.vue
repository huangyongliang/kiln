<script setup>
import { computed } from "vue";

const props = defineProps({
  status: { type: String, default: "" },
  label: { type: String, default: "" },
});

const MAP = {
  queued: { label: "排队中", cls: "run" },
  running: { label: "进行中", cls: "run" },
  success: { label: "成功", cls: "ok" },
  failure: { label: "失败", cls: "fail" },
  unstable: { label: "不稳定", cls: "review" },
  aborted: { label: "已中止", cls: "fail" },
  not_built: { label: "未构建", cls: "draft" },
  unknown: { label: "未知", cls: "draft" },
  draft: { label: "草稿", cls: "draft" },
  in_review: { label: "审核中", cls: "review" },
  published: { label: "已发布", cls: "ok" },
  active: { label: "启用", cls: "ok" },
  disabled: { label: "停用", cls: "draft" },
};

const view = computed(() => MAP[props.status] || { label: props.status || "未知", cls: "draft" });
const text = computed(() => props.label || view.value.label);
</script>

<template>
  <span class="tag" :class="view.cls">{{ text }}</span>
</template>

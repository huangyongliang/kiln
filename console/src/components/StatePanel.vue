<script setup>
import { computed } from "vue";
import { RouterLink } from "vue-router";

import { permissionDescription } from "../access";

const props = defineProps({
  loading: { type: Boolean, default: false },
  empty: { type: Boolean, default: false },
  error: { type: String, default: "" },
  emptyText: { type: String, default: "" },
  forbidden: { type: Boolean, default: false },
  forbiddenCode: { type: String, default: "" },
});

defineEmits(["retry"]);

const needed = computed(() => permissionDescription(props.forbiddenCode));
</script>

<template>
  <section v-if="forbidden" class="forbidden" role="alert">
    <h2>你没有查看此页面的权限</h2>
    <p>需要权限：{{ needed }}</p>
    <p class="mono">{{ forbiddenCode }}</p>
    <RouterLink class="btn" to="/">返回概览</RouterLink>
  </section>
  <div v-else-if="loading" class="skeleton" aria-busy="true" aria-live="polite">
    <span></span>
    <span></span>
    <span></span>
  </div>
  <div v-else-if="error" class="banner" role="alert">
    <p>{{ error }}</p>
    <button type="button" class="btn btn-ghost" @click="$emit('retry')">重试</button>
  </div>
  <div v-else-if="empty" class="empty">
    <strong>{{ emptyText }}</strong>
    <slot name="action" />
  </div>
  <slot v-else />
</template>

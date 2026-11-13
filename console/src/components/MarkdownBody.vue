<script setup>
import { computed } from "vue";

import { renderChatMarkdown } from "../markdown";

const props = defineProps({
  text: { type: String, default: "" },
});

const html = computed(() => renderChatMarkdown(props.text));
</script>

<template>
  <div class="markdown-body" v-html="html"></div>
</template>

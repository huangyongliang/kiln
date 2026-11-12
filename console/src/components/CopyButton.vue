<script setup>
import { ref } from "vue";

import { copyText } from "../clipboard";

const props = defineProps({
  value: { type: String, required: true },
  label: { type: String, default: "复制" },
});

const manual = ref(false);

async function copy() {
  manual.value = false;
  const ok = await copyText(props.value);
  if (!ok) {
    manual.value = true;
  }
}
</script>

<template>
  <span class="copy-control">
    <button type="button" class="btn btn-ghost" @click="copy">{{ label }}</button>
    <label v-if="manual" class="copy-fallback">
      请手动复制
      <input type="text" readonly :value="value" @focus="$event.target.select()" />
    </label>
  </span>
</template>

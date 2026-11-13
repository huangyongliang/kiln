<script setup>
import { onMounted, ref } from "vue";

defineProps({
  title: { type: String, required: true },
  busy: { type: Boolean, default: false },
  error: { type: String, default: "" },
  done: { type: Boolean, default: false },
});

const emit = defineEmits(["close", "submit"]);
const dialog = ref(null);

onMounted(() => {
  dialog.value?.showModal();
});

function close() {
  dialog.value?.close();
}
</script>

<template>
  <dialog ref="dialog" class="modal" @close="emit('close')">
    <form @submit.prevent="emit('submit')">
      <h2>{{ title }}</h2>
      <slot />
      <p v-if="error" class="message error" role="alert">{{ error }}</p>
      <div class="modal-actions">
        <button v-if="!done" type="button" class="btn btn-secondary" @click="close">取消</button>
        <button v-if="!done" type="submit" class="btn" :disabled="busy">
          <span v-if="busy" class="spinner" aria-hidden="true"></span>
          提交
        </button>
        <button v-if="done" type="button" class="btn" @click="close">关闭</button>
      </div>
    </form>
  </dialog>
</template>

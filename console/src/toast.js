import { ref } from "vue";

export const toasts = ref([]);

export function pushToast(text) {
  const id = `${Date.now()}-${Math.random()}`;
  toasts.value = [...toasts.value, { id, text }];
  window.setTimeout(() => {
    toasts.value = toasts.value.filter((item) => item.id !== id);
  }, 2600);
}

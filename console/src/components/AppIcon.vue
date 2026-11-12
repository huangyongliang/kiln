<script setup>
const props = defineProps({
  name: { type: String, required: true },
  size: { type: Number, default: 18 },
});
</script>

<template>
  <svg
    v-if="name === 'mark'"
    :width="size"
    :height="size"
    viewBox="0 0 32 32"
    fill="none"
    aria-hidden="true"
  >
    <path
      d="M6 25.5V16.2C6 9.8 10.4 6 16 6s10 3.8 10 10.2v9.3"
      stroke="currentColor"
      stroke-width="2.1"
      stroke-linecap="round"
    />
    <path d="M4 25.5h24" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" />
    <circle cx="16" cy="18.5" r="3.1" fill="#E06A35" />
  </svg>
  <svg
    v-else-if="name === 'overview'"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
    <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
    <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
    <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
  </svg>
  <svg
    v-else-if="name === 'builds'"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path d="M5 19V9l7-5 7 5v10" />
    <path d="M9 19v-6h6v6" />
  </svg>
  <svg
    v-else-if="name === 'artifacts'"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path d="M12 3 4.5 7v10L12 21l7.5-4V7L12 3z" />
    <path d="M4.5 7 12 11l7.5-4M12 11v10" />
  </svg>
  <svg
    v-else-if="name === 'skills'"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <circle cx="6" cy="6" r="2.2" />
    <circle cx="18" cy="12" r="2.2" />
    <circle cx="6" cy="18" r="2.2" />
    <path d="M8.2 6h5.3A3.5 3.5 0 0 1 17 9.5V10M8.2 18h5.3A3.5 3.5 0 0 0 17 14.5V14" />
  </svg>
  <svg
    v-else-if="name === 'access'"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <circle cx="9" cy="8" r="2.4" />
    <path d="M4.5 19v-1.2A3.8 3.8 0 0 1 8.3 14h1.4A3.8 3.8 0 0 1 13.5 17.8V19" />
    <circle cx="16.5" cy="8.5" r="2" />
    <path d="M19.5 19v-1a3.2 3.2 0 0 0-2.4-3.1" />
  </svg>
  <svg
    v-else-if="name === 'chat'"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path d="M5 17.5 4 21l3.6-1.4A8.5 8.5 0 1 0 5 17.5z" />
  </svg>
  <svg
    v-else-if="name === 'lock'"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <rect x="5" y="11" width="14" height="9" rx="2" />
    <path d="M8 11V8a4 4 0 0 1 8 0v3" />
  </svg>
</template>

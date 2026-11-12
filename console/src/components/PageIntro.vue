<script setup>
defineProps({
  title: { type: String, required: true },
  lead: { type: String, default: "" },
  note: { type: String, default: "" },
});
</script>

<template>
  <header class="page-head">
    <div>
      <slot name="meta" />
      <h1>{{ title }}</h1>
      <p v-if="lead" class="lead">{{ lead }}</p>
      <details v-if="note" class="note">
        <summary>说明</summary>
        <p>{{ note }}</p>
      </details>
    </div>
    <div v-if="$slots.actions" class="actions">
      <slot name="actions" />
    </div>
  </header>
</template>

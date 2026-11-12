<script setup>
defineProps({
  title: { type: String, required: true },
  lead: { type: String, required: true },
});
</script>

<template>
  <header>
    <h1>{{ title }}</h1>
    <p class="lead">{{ lead }}</p>
  </header>
</template>

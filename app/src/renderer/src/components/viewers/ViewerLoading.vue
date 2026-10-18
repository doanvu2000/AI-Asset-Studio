<script setup lang="ts">
import { LoaderCircle } from '@lucide/vue'
</script>

<template>
  <div class="vw vw-backdrop">
    <div class="vw-state" role="status"><LoaderCircle :size="22" class="spin" /><span>Đang mở trình xem</span></div>
  </div>
</template>

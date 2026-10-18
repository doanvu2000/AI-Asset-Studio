<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MotionConfig } from 'motion-v'
import { onKeyStroke } from '@vueuse/core'
import AppSidebar from './components/AppSidebar.vue'
import CoreBanner from './components/CoreBanner.vue'
import CoreOffline from './components/CoreOffline.vue'
import CommandPalette from './components/CommandPalette.vue'
import ToastHost from './components/ToastHost.vue'
import ResizeHandle from './components/ResizeHandle.vue'
import { usePaneWidth } from './composables/paneSize'
import { useShellIntegration } from './composables/shellIntegration'
import { useConnection } from './stores/connection'

const conn = useConnection()
const router = useRouter()
const route = useRoute()
const SIDEBAR = { default: 232, min: 184, max: 320, rail: 60 }
const sidebarW = usePaneWidth('sidebar', SIDEBAR.default)

onMounted(() => void conn.init())
useShellIntegration()

// Before the first connection there is nothing to show, so a dead core gets the full screen instead of a banner.
// Settings stays reachable though: it is where a broken tool path gets fixed.
const offline = computed(
  () => !conn.core.mock && !conn.transport && ['error', 'restarting', 'stopped'].includes(conn.core.state) && route.name !== 'settings'
)

const NAV = ['/', '/new', '/runs', '/gates', '/gallery', '/recipes', '/batch']
onKeyStroke(
  (e) => (e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey,
  (e) => {
    const n = Number(e.key)
    if (n >= 1 && n <= NAV.length) {
      e.preventDefault()
      void router.push(NAV[n - 1]!)
    } else if (e.key.toLowerCase() === 'n') {
      e.preventDefault()
      void router.push('/new')
    }
  }
)
</script>

<template>
  <MotionConfig reduced-motion="user">
    <div class="shell">
      <AppSidebar :style="{ '--pane-w': `${sidebarW}px` }" :compact="sidebarW <= SIDEBAR.rail" />
      <ResizeHandle
        v-model="sidebarW"
        label="Thanh bên"
        :default-size="SIDEBAR.default"
        :min="SIDEBAR.min"
        :max="SIDEBAR.max"
        :collapsed-size="SIDEBAR.rail"
        :collapse-below="140"
      />
      <main class="content">
        <CoreBanner v-if="!offline" />
        <CoreOffline v-if="offline" />
        <RouterView v-else v-slot="{ Component, route }">
          <Transition name="page" mode="out-in">
            <component :is="Component" :key="['run', 'recipe', 'batch-detail'].includes(String(route.name)) ? route.fullPath : String(route.name)" />
          </Transition>
        </RouterView>
      </main>
    </div>
    <ToastHost />
    <CommandPalette />
  </MotionConfig>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100%;
}
.content {
  position: relative;
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-content);
  box-shadow: -0.5px 0 0 var(--separator);
}
.content > :deep(.view) {
  flex: 1;
  min-height: 0;
}
.page-enter-active {
  transition:
    opacity var(--t-base) var(--ease-out),
    transform var(--t-base) var(--ease-spring);
}
.page-leave-active {
  transition: opacity 110ms var(--ease-in);
}
.page-enter-from {
  opacity: 0;
  transform: translateY(6px);
}
.page-leave-to {
  opacity: 0;
}
</style>

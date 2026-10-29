<script setup lang="ts">
import { ref } from 'vue'
import { FolderOpen, LoaderCircle, Puzzle } from '@lucide/vue'
import type { EnginePluginInstall } from '../../../preload/types'
import { useToasts } from '@/stores/toasts'

const toasts = useToasts()
const canInstall = !!window.studio?.installEnginePlugin
const busy = ref(false)
const last = ref<EnginePluginInstall | null>(null)

async function install(): Promise<void> {
  if (busy.value || !window.studio) return
  busy.value = true
  try {
    const res = await window.studio.installEnginePlugin()
    if (!res) return
    last.value = res
    toasts.push(res.engine === 'unity' ? 'Đã cài package Unity' : 'Đã cài addon Godot', 'success', 2600)
  } catch (e) {
    toasts.error(e, 'Không cài được plugin')
  } finally {
    busy.value = false
  }
}

async function reveal(): Promise<void> {
  if (!last.value) return
  try {
    await window.studio?.revealPath(last.value.dest)
  } catch (e) {
    toasts.error(e, 'Không mở được thư mục')
  }
}
</script>

<template>
  <section v-if="canInstall" class="plugin card">
    <header>
      <span class="ic"><Puzzle :size="17" :stroke-width="2" /></span>
      <div class="names">
        <h3 class="headline">Plugin Unity / Godot</h3>
        <p class="caption">
          Chạy quy trình, duyệt bước chờ và nhận output ngay trong editor. Chuột phải asset: Restyle, Tô lại texture, Biến thể kẻ địch,
          Thêm vào Dự án.
        </p>
      </div>
      <button class="btn btn-primary" :disabled="busy" @click="install">
        <LoaderCircle v-if="busy" class="spin" />
        <FolderOpen v-else />
        Cài plugin vào project…
      </button>
    </header>
    <Transition name="fade">
      <p v-if="last" class="caption done">
        Đã cài vào <button class="link mono selectable" @click="reveal">{{ last.dest }}</button>.
        {{
          last.engine === 'unity'
            ? 'Mở Unity: menu Window → AI Asset Studio.'
            : 'Mở Godot: dock AI Asset Studio bên phải (plugin đã bật trong project.godot).'
        }}
      </p>
    </Transition>
    <p class="caption secondary">Plugin tự tìm app đang chạy trên máy này; không cần copy token.</p>
  </section>
</template>

<style scoped>
.plugin {
  max-width: 860px;
  margin: 18px auto 0;
  display: grid;
  gap: 10px;
  padding: 14px 16px;
}
header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.ic {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 9px;
  color: var(--accent);
  background: var(--accent-soft);
  flex: none;
}
.names {
  flex: 1;
  min-width: 0;
}
.link {
  padding: 0;
  border: 0;
  background: none;
  color: var(--accent);
  cursor: pointer;
  word-break: break-all;
  text-align: left;
}
.link:hover {
  text-decoration: underline;
}
@media (prefers-reduced-motion: reduce) {
  .fade-enter-active,
  .fade-leave-active {
    transition: none;
  }
}
</style>

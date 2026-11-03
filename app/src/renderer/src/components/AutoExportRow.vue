<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Check, CircleAlert, FolderOpen, LoaderCircle } from '@lucide/vue'
import type { ProjectRow } from '@/api/types'
import { autoExportText, lastAutoExport, type AutoExportPatch, type ProjectWithAutoExport } from '@/composables/autoExport'
import { useProjects } from '@/stores/projects'
import { useToasts } from '@/stores/toasts'
import RelTime from './RelTime.vue'

const props = defineProps<{ project: ProjectRow }>()
const projects = useProjects()
const toasts = useToasts()

const p = computed(() => props.project as ProjectWithAutoExport)
const on = computed(() => !!p.value.auto_export)
const saved = computed(() => p.value.auto_export_dir ?? '')
const dir = ref(saved.value)
const saving = ref(false)
watch(saved, (v) => (dir.value = v))

const ENGINE_NAMES: Record<string, string> = { flutter: 'Flutter', android: 'Android', ios: 'iOS', unity: 'Unity', godot: 'Godot' }
const engineName = computed(() => ENGINE_NAMES[props.project.engine_target])
const isAbs = (s: string): boolean => /^([a-zA-Z]:[\\/]|\/)/.test(s.trim())
const dirError = computed(() => (dir.value.trim() && !isAbs(dir.value) ? 'Cần đường dẫn đầy đủ, ví dụ D:\\Games\\MyGame' : null))
const last = computed(() => lastAutoExport(props.project.id))

const setupHint = computed(() => {
  if (!on.value) return null
  if (!engineName.value) return 'Chọn engine đích ở trên để bắt đầu xuất.'
  if (!saved.value.trim()) return 'Chọn thư mục project game để bắt đầu xuất.'
  return null
})

async function save(body: AutoExportPatch): Promise<void> {
  if (saving.value) return
  saving.value = true
  try {
    await projects.patch(props.project.id, body)
  } catch (e) {
    dir.value = saved.value
    toasts.error(e, 'Không lưu được tuỳ chọn tự xuất')
  } finally {
    saving.value = false
  }
}

function toggle(e: Event): void {
  const next = (e.target as HTMLInputElement).checked
  void save({ auto_export: next })
}

function commitDir(): void {
  const v = dir.value.trim()
  if (v === saved.value || dirError.value) return
  void save({ auto_export_dir: v })
}

async function pick(): Promise<void> {
  if (!window.studio) return void toasts.push('Chọn thư mục chỉ có trong ứng dụng desktop; dán đường dẫn vào ô', 'info')
  try {
    const path = await window.studio.pickPath({ kind: 'dir', title: 'Chọn thư mục project game', defaultPath: dir.value || undefined })
    if (path) {
      dir.value = path
      commitDir()
    }
  } catch (e) {
    toasts.error(e, 'Không mở được hộp chọn thư mục')
  }
}
</script>

<template>
  <div class="auto">
    <label class="chk" for="auto-export-on">
      <input id="auto-export-on" type="checkbox" role="switch" :checked="on" :disabled="saving" @change="toggle" />
      <span class="section-label">Tự xuất khi duyệt</span>
    </label>
    <span class="caption sub">Duyệt xong là xuất ngay vào project game{{ engineName ? ` (${engineName})` : '' }}.</span>

    <div v-if="on" class="dest">
      <input
        v-model="dir"
        class="field"
        spellcheck="false"
        placeholder="D:\Games\MyGame"
        aria-label="Thư mục project game"
        :aria-invalid="!!dirError"
        @blur="commitDir"
        @keydown.enter.prevent="commitDir"
      />
      <button type="button" class="btn" @click="pick"><FolderOpen /> Chọn…</button>
      <LoaderCircle v-if="saving" class="spin" :size="16" aria-label="Đang lưu" />
    </div>
    <p v-if="dirError" class="note err" role="alert">{{ dirError }}</p>
    <p v-else-if="setupHint" class="note warn">{{ setupHint }}</p>
    <p v-if="on && last" class="note" :class="last.ok ? 'ok' : 'err'" role="status">
      <Check v-if="last.ok" :size="14" /><CircleAlert v-else :size="14" />
      <span class="msg">{{ autoExportText(last) }}</span>
      <RelTime :ts="last.ts" />
    </p>
  </div>
</template>

<style scoped>
.auto {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: var(--sp-2) var(--sp-3);
  padding: var(--sp-3) var(--sp-4);
}
.chk {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
  cursor: pointer;
}
.chk input {
  width: 16px;
  height: 16px;
  accent-color: var(--accent);
  cursor: pointer;
}
.sub {
  min-width: 0;
}
.dest,
.note {
  grid-column: 1 / -1;
}
.dest {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.dest .field {
  flex: 1;
  min-width: 0;
  font-family: var(--font-mono, ui-monospace, monospace);
}
.note {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  margin: 0;
  font-size: var(--fs-caption);
  color: var(--ink-2);
}
.note .msg {
  min-width: 0;
  overflow-wrap: anywhere;
}
.note.ok {
  color: var(--st-ok);
}
.note.err {
  color: var(--st-fail);
}
.note.warn {
  color: var(--warn);
}
@media (max-width: 640px) {
  .auto {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

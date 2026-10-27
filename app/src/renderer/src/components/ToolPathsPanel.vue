<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { CircleAlert, CircleCheck, CircleX, FileCog, FolderOpen, LoaderCircle, RefreshCw, RotateCw, Save } from '@lucide/vue'
import type { ToolKey, ToolPath } from '@/api/types'
import { api, useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { useCoreRestart } from '@/composables/coreRestart'

const conn = useConnection()
const toasts = useToasts()
const restart = useCoreRestart()
const tools = ref<ToolPath[]>([])
const draft = ref<Partial<Record<ToolKey, string>>>({})
const settingsFile = ref('')
const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)
const canPick = !!window.studio

const changed = computed(() => tools.value.filter((t) => (draft.value[t.key] ?? t.path).trim() !== t.path))

function apply(list: ToolPath[], file: string): void {
  tools.value = list
  settingsFile.value = file
  draft.value = Object.fromEntries(list.map((t) => [t.key, t.path]))
}

async function load(): Promise<void> {
  if (!conn.transport) return
  loading.value = true
  error.value = null
  try {
    const r = await api().toolPaths()
    apply(r.tools, r.settings_file)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function save(): Promise<void> {
  if (!changed.value.length || saving.value) return
  saving.value = true
  error.value = null
  try {
    const r = await api().saveToolPaths(Object.fromEntries(changed.value.map((t) => [t.key, draft.value[t.key] ?? ''])))
    apply(r.tools, r.settings_file)
    toasts.push('Đã lưu đường dẫn', 'success', 2200)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    saving.value = false
  }
}

async function pick(t: ToolPath): Promise<void> {
  const current = (draft.value[t.key] ?? t.path).trim()
  try {
    const chosen = await window.studio?.pickPath({
      kind: t.kind,
      title: t.label,
      defaultPath: current || undefined,
      extensions: t.kind === 'file' && window.studio.platform === 'win32' ? ['exe'] : undefined
    })
    if (chosen) draft.value[t.key] = chosen
  } catch (e) {
    toasts.error(e, 'Không mở được hộp chọn')
  }
}

async function openSettingsFile(): Promise<void> {
  try {
    await window.studio?.openSettingsFile()
  } catch (e) {
    toasts.error(e, 'Không mở được settings.json')
  }
}

function state(t: ToolPath): 'ok' | 'warn' | 'missing' {
  return !t.exists ? 'missing' : t.ok ? 'ok' : 'warn'
}

watch(
  () => conn.transport,
  (t) => t && void load(),
  { immediate: true }
)
</script>

<template>
  <div class="tools">
    <header class="t-head">
      <div>
        <h2 class="content-title">Đường dẫn công cụ</h2>
        <p class="secondary">ComfyUI, model, Blender, Codex nằm ngoài app — chỉ đúng chỗ cài trên máy này.</p>
      </div>
      <div v-if="conn.transport" class="actions">
        <button class="btn" :disabled="loading" @click="load">
          <RefreshCw :class="{ spin: loading }" />
          Kiểm tra lại
        </button>
        <button class="btn btn-primary" :disabled="!changed.length || saving" @click="save">
          <LoaderCircle v-if="saving" class="spin" />
          <Save v-else />
          Lưu
        </button>
      </div>
    </header>

    <div v-if="!conn.transport" class="card offline" role="status">
      <p class="headline">Core chưa chạy nên chưa đọc được đường dẫn</p>
      <p class="secondary">
        Sửa tay trong <span class="mono">settings.json</span> (khoá <span class="mono">comfy_dir</span>, <span class="mono">blender_exe</span>…), lưu
        lại rồi khởi động lại core.
      </p>
      <div class="actions">
        <button v-if="canPick" class="btn" @click="openSettingsFile"><FileCog /> Mở settings.json</button>
        <button v-if="restart.available" class="btn btn-primary" :disabled="restart.busy.value" @click="restart.run">
          <LoaderCircle v-if="restart.busy.value" class="spin" />
          <RotateCw v-else />
          Khởi động lại core
        </button>
      </div>
    </div>

    <template v-else>
      <p v-if="error" class="err" role="alert">{{ error }}</p>

      <div class="card rows" :aria-busy="loading">
        <div v-for="t in tools" :key="t.key" class="row">
          <label :for="`tool-${t.key}`" class="lbl">{{ t.label }}</label>
          <div class="val">
            <div class="input-row">
              <input
                :id="`tool-${t.key}`"
                v-model="draft[t.key]"
                class="field mono"
                spellcheck="false"
                :placeholder="t.kind === 'dir' ? 'Thư mục…' : 'File .exe…'"
                @keydown.enter="save"
              />
              <button v-if="canPick" type="button" class="btn pick" :aria-label="`Chọn ${t.label}`" @click="pick(t)">
                <FolderOpen />
                Chọn…
              </button>
            </div>
            <p class="status caption" :class="state(t)">
              <CircleCheck v-if="state(t) === 'ok'" :size="13" />
              <CircleAlert v-else-if="state(t) === 'warn'" :size="13" />
              <CircleX v-else :size="13" />
              <span>{{ [t.version, t.detail].filter(Boolean).join(' · ') || (t.exists ? 'Tìm thấy' : 'Không tìm thấy') }}</span>
              <span v-if="(draft[t.key] ?? t.path).trim() !== t.path" class="pending">· chưa lưu</span>
            </p>
          </div>
        </div>
        <p v-if="!tools.length && loading" class="caption empty">Đang kiểm tra…</p>
      </div>

      <p class="caption secondary">
        Lưu vào <span class="mono selectable">{{ settingsFile }}</span>. ComfyUI / Blender đang chạy dùng đường dẫn mới sau khi dừng rồi khởi động lại (trang Tổng quan).
      </p>
    </template>
  </div>
</template>

<style scoped>
.tools {
  max-width: 860px;
  margin: 0 auto;
  display: grid;
  gap: 18px;
  animation: fade-up var(--t-slow) var(--ease-out);
}
.content-title {
  font-size: 22px;
  font-weight: 600;
  line-height: 28px;
  letter-spacing: -0.015em;
}
.t-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.t-head .secondary {
  margin-top: 2px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.offline {
  display: grid;
  gap: 8px;
  padding: 16px 18px;
}
.offline .actions {
  margin-top: 4px;
}
.err {
  color: var(--st-fail);
  font-size: var(--fs-callout);
}
.rows {
  padding: 4px 18px;
  display: grid;
}
.row {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: 16px;
  padding: 11px 0;
  align-items: start;
}
.row + .row {
  border-top: 0.5px solid var(--separator);
}
.lbl {
  padding-top: 7px;
  font-weight: 500;
  color: var(--text-2);
}
.val {
  display: grid;
  gap: 5px;
  min-width: 0;
}
.input-row {
  display: flex;
  gap: 8px;
  min-width: 0;
}
.input-row .field {
  flex: 1;
  min-width: 0;
}
.pick {
  flex: none;
  height: var(--ctl-h);
}
.mono {
  font-family: var(--mono);
}
.field.mono {
  font-size: 12.5px;
}
.status {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
  transition: color var(--t-fast);
}
.status span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.status.ok {
  color: var(--st-ok);
}
.status.warn {
  color: var(--st-waiting);
}
.status.missing {
  color: var(--st-fail);
}
.pending {
  color: var(--text-3);
}
.empty {
  padding: 12px 0;
}
.spin {
  animation: spin 1s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .tools {
    animation: none;
  }
}
</style>

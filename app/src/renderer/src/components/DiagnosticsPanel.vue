<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ClipboardCopy, FileArchive, FolderOpen, LoaderCircle, RefreshCw } from '@lucide/vue'
import type { DiagInfo, DiagPayload } from '../../../preload/types'
import RelTime from '@/components/RelTime.vue'
import { collectPayload, useDiagnosticsActions } from '@/composables/diagnostics'
import { bytes, kindName } from '@/composables/format'
import { useConnection } from '@/stores/connection'

const conn = useConnection()
const { busy, available, copyLogs, exportLogs, openLogsDir } = useDiagnosticsActions()
const info = ref<DiagInfo | null>(null)
const payload = ref<DiagPayload | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

const CORE_STATE: Record<string, string> = {
  ready: 'Đang chạy',
  starting: 'Đang khởi động',
  restarting: 'Đang khởi động lại',
  stopped: 'Đã dừng',
  error: 'Lỗi'
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const [i, p] = await Promise.all([window.studio?.diagInfo() ?? Promise.resolve(null), collectPayload()])
    info.value = i
    payload.value = p
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

const mode = computed(() => {
  const i = info.value
  if (!i) return ''
  if (i.mock) return 'Mock (dữ liệu giả)'
  if (i.portable) return 'Bản portable'
  return i.packaged ? 'Bản cài đặt' : 'Dev'
})

const core = computed(() => {
  const c = conn.core
  const d = payload.value?.diagnostics?.core
  const parts = [CORE_STATE[c.state] ?? c.state]
  if (d) parts.push(`core ${d.version}`, `Python ${d.python}`)
  if (c.pid) parts.push(`pid ${c.pid}`)
  if (c.attached) parts.push('dùng chung với MCP')
  return parts.join(' · ')
})

const coreTone = computed(() => (conn.core.state === 'ready' ? (payload.value?.diagnostics ? 'ok' : 'warn') : 'missing'))
const failed = computed(() => payload.value?.diagnostics?.failed_steps.slice(0, 5) ?? [])
const logs = computed(() => info.value?.logs ?? [])

onMounted(load)
</script>

<template>
  <div class="diag">
    <header class="d-head">
      <h2 class="large-title">Nhật ký chẩn đoán</h2>
      <p class="secondary">Gửi kèm khi báo lỗi. Token truy cập đã được che trước khi copy hoặc xuất.</p>
    </header>
    <div class="actions">
      <button class="btn btn-primary" :disabled="!available || !!busy" @click="copyLogs">
        <LoaderCircle v-if="busy === 'copy'" class="spin" />
        <ClipboardCopy v-else />
        Copy nhật ký
      </button>
      <button class="btn" :disabled="!available || !!busy" @click="exportLogs">
        <LoaderCircle v-if="busy === 'export'" class="spin" />
        <FileArchive v-else />
        Xuất file…
      </button>
      <button class="btn" :disabled="!available" @click="openLogsDir"><FolderOpen /> Mở thư mục log</button>
      <button class="btn btn-plain btn-icon refresh" :disabled="loading" aria-label="Tải lại" title="Tải lại" @click="load">
        <RefreshCw :class="{ spin: loading }" />
      </button>
    </div>
    <p v-if="error" class="err" role="alert">{{ error }}</p>
    <p v-if="!available" class="caption secondary">Chỉ dùng được trong app (không có cầu nối Electron).</p>

    <div class="card rows" :aria-busy="loading">
      <div class="row">
        <span class="lbl">Phiên bản</span>
        <span class="val">{{ info ? `AI Asset Studio ${info.appVersion} · Electron ${info.electron}` : '…' }}</span>
      </div>
      <div class="row">
        <span class="lbl">Nền tảng</span>
        <span class="val">{{ info ? `${info.platform} ${info.arch} · ${info.osRelease} · ${mode}` : '…' }}</span>
      </div>
      <div class="row">
        <span class="lbl">Core</span>
        <span class="val">
          <span class="dot" :class="coreTone" aria-hidden="true" />
          {{ core }}
          <span v-if="payload && !payload.diagnostics" class="caption warn-txt">· {{ payload.error }}</span>
        </span>
      </div>
      <div class="row">
        <span class="lbl">Thư mục dữ liệu</span>
        <span class="val mono selectable">{{ info?.dataDir ?? '…' }}</span>
      </div>
      <div class="row">
        <span class="lbl">Thư mục log</span>
        <span class="val mono selectable">{{ info?.logsDir ?? '…' }}</span>
      </div>
    </div>

    <section class="grp">
      <h3 class="section-label">Bước lỗi gần đây</h3>
      <div class="card list">
        <div v-for="f in failed" :key="f.step_id" class="fail">
          <div class="f-top">
            <strong>{{ kindName(f.kind) }}</strong>
            <span class="caption mono">{{ f.code ?? 'lỗi' }}</span>
            <RelTime v-if="f.ended_at" class="caption when" :ts="f.ended_at" />
          </div>
          <p class="caption f-msg selectable">{{ f.message || '(không có mô tả)' }}</p>
        </div>
        <p v-if="!failed.length" class="caption empty">{{ payload?.diagnostics ? 'Chưa có bước nào lỗi.' : 'Chưa lấy được từ core.' }}</p>
      </div>
    </section>

    <section class="grp">
      <h3 class="section-label">File log</h3>
      <div class="card list">
        <div v-for="l in logs" :key="l.name" class="log">
          <span class="mono">{{ l.name }}</span>
          <span class="caption num">{{ bytes(l.bytes) }}</span>
          <RelTime class="caption when" :ts="l.modified / 1000" />
        </div>
        <p v-if="!logs.length" class="caption empty">Chưa có file log.</p>
      </div>
      <p class="caption secondary">“Copy nhật ký” lấy 200 dòng cuối mỗi log; “Xuất file…” lưu file .zip đầy đủ kèm chẩn đoán core.</p>
    </section>
  </div>
</template>

<style scoped>
.diag {
  max-width: 860px;
  margin: 0 auto;
  display: grid;
  gap: 18px;
  padding-bottom: 24px;
  animation: fade-up var(--t-slow) var(--ease-out);
}
.d-head {
  display: grid;
  gap: 2px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: -6px;
}
.refresh {
  margin-left: auto;
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
  align-items: center;
}
.row + .row {
  border-top: 0.5px solid var(--separator);
}
.lbl {
  font-weight: 500;
  color: var(--text-2);
}
.val {
  min-width: 0;
  overflow-wrap: anywhere;
}
.mono {
  font-family: var(--mono);
  font-size: 12.5px;
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 4px;
  border-radius: 50%;
  vertical-align: 1px;
  transition: background-color var(--t-base);
}
.dot.ok {
  background: var(--st-ok);
}
.dot.warn {
  background: var(--st-waiting);
}
.dot.missing {
  background: var(--st-fail);
}
.warn-txt {
  color: var(--st-waiting);
}
.grp {
  display: grid;
  gap: 8px;
}
.list {
  padding: 4px 18px;
  display: grid;
}
.fail,
.log {
  padding: 9px 0;
  min-width: 0;
}
.fail + .fail,
.log + .log {
  border-top: 0.5px solid var(--separator);
}
.f-top,
.log {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.when {
  margin-left: auto;
  color: var(--text-3);
}
.f-msg {
  margin: 3px 0 0;
  font-family: var(--mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 4.5em;
  overflow: hidden;
}
.empty {
  padding: 12px 0;
}
.spin {
  animation: spin 1s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .diag {
    animation: none;
  }
}
</style>

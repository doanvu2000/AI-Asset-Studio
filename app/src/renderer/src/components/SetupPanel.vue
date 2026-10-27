<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Ban, CircleAlert, CircleCheck, CircleX, Download, FolderOpen, LoaderCircle, LogIn, RotateCw } from '@lucide/vue'
import type { SetupItem, SetupJob, SetupPlan } from '@/api/types'
import { api, useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { bytes } from '@/composables/format'

const conn = useConnection()
const toasts = useToasts()
const router = useRouter()
const canPick = !!window.studio

const plan = ref<SetupPlan | null>(null)
const job = ref<SetupJob | null>(null)
const toolsDir = ref('')
const modelsDir = ref('')
const packs = ref<Set<string>>(new Set())
const blender = ref(true)
const claude = ref(true)
const loading = ref(false)
const starting = ref(false)
const error = ref<string | null>(null)
const showForm = ref(true)
let timer: ReturnType<typeof setInterval> | null = null
let loadSeq = 0

async function load(dirs: { tools_dir?: string; models_dir?: string } = {}): Promise<void> {
  if (!conn.transport) return
  const seq = ++loadSeq
  loading.value = true
  error.value = null
  try {
    const r = await api().setupPlan(dirs)
    if (seq !== loadSeq) return
    const first = !plan.value
    plan.value = r.plan
    if (first) {
      toolsDir.value = r.plan.tools_dir
      modelsDir.value = r.plan.models_dir
    }
    if (first) {
      packs.value = new Set(r.plan.packs.map((p) => p.id))
      blender.value = !r.plan.components.blender.installed
      claude.value = !r.plan.components.claude.installed
    }
    if (r.job) {
      job.value = r.job
      if (first) showForm.value = r.job.state !== 'running' && r.job.state !== 'done'
      if (r.job.state === 'running') poll()
    }
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function poll(): void {
  if (timer) return
  timer = setInterval(async () => {
    try {
      const j = await api().setupJob()
      job.value = j
      if (!j || j.state !== 'running') {
        stopPoll()
        if (j?.state === 'done') toasts.push('Cài đặt xong', 'success', 3000)
        else if (j?.state === 'error') toasts.push(j.error ?? 'Cài đặt lỗi', 'error', 5000)
        await load({ tools_dir: toolsDir.value, models_dir: modelsDir.value })
      }
    } catch {
      /* core restarting: keep polling */
    }
  }, 1000)
}
function stopPoll(): void {
  if (timer) clearInterval(timer)
  timer = null
}
onBeforeUnmount(stopPoll)

watch(
  () => conn.transport,
  (t) => t && void load(),
  { immediate: true }
)

async function pick(which: 'tools' | 'models'): Promise<void> {
  const target = which === 'tools' ? toolsDir : modelsDir
  try {
    const chosen = await window.studio?.pickPath({ kind: 'dir', title: which === 'tools' ? 'Thư mục công cụ' : 'Thư mục model', defaultPath: target.value || undefined })
    if (chosen) {
      target.value = chosen
      await load({ tools_dir: toolsDir.value, models_dir: modelsDir.value })
    }
  } catch (e) {
    toasts.error(e, 'Không mở được hộp chọn')
  }
}

function togglePack(id: string, required: boolean): void {
  if (required) return
  const next = new Set(packs.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  packs.value = next
}

const download = computed(() => {
  const p = plan.value
  if (!p) return { models: 0, tools: 0 }
  const models = p.packs.filter((x) => x.required || packs.value.has(x.id)).reduce((a, x) => a + x.missing_size, 0)
  const c = p.components
  const tools = (c.comfyui.installed ? 0 : (c.comfyui.size ?? 0)) + (blender.value && !c.blender.installed ? (c.blender.size ?? 0) : 0)
  return { models, tools }
})
const nothingToDo = computed(() => {
  const c = plan.value?.components
  return !!c && download.value.models === 0 && c.comfyui.installed && (!blender.value || c.blender.installed) && (!claude.value || c.claude.installed)
})
const spaceProblem = computed(() => {
  const p = plan.value
  if (!p) return null
  const free = p.free.models
  if (free !== null && free < download.value.models + download.value.tools) return `Ổ chứa model còn ${bytes(free)}, cần khoảng ${bytes(download.value.models + download.value.tools)}.`
  return null
})

async function start(): Promise<void> {
  if (starting.value) return
  starting.value = true
  error.value = null
  try {
    job.value = await api().startSetup({
      tools_dir: toolsDir.value.trim(),
      models_dir: modelsDir.value.trim(),
      packs: [...packs.value],
      blender: blender.value,
      claude: claude.value
    })
    showForm.value = false
    poll()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    starting.value = false
  }
}

async function cancel(): Promise<void> {
  try {
    job.value = await api().cancelSetup()
  } catch (e) {
    toasts.error(e, 'Không huỷ được')
  }
}

async function claudeLogin(): Promise<void> {
  try {
    await api().claudeLogin()
    toasts.push('Đã mở cửa sổ Claude — làm theo hướng dẫn đăng nhập trong đó', 'info', 5000)
  } catch (e) {
    toasts.error(e, 'Không mở được Claude')
  }
}

const pct = computed(() => (job.value && job.value.bytes_total ? Math.min(100, (100 * job.value.bytes_done) / job.value.bytes_total) : 0))
const eta = computed(() => {
  const j = job.value
  if (!j || j.state !== 'running' || !j.speed) return null
  const s = (j.bytes_total - j.bytes_done) / j.speed
  if (s < 60) return 'dưới 1 phút'
  if (s < 3600) return `${Math.round(s / 60)} phút`
  return `${Math.floor(s / 3600)} giờ ${Math.round((s % 3600) / 60)} phút`
})
const STATE_TEXT: Record<SetupItem['state'], string> = {
  pending: 'Chờ',
  running: 'Đang chạy',
  done: 'Xong',
  skipped: 'Đã có',
  error: 'Lỗi',
  cancelled: 'Đã huỷ'
}
const claudeReady = computed(() => !!plan.value?.components.claude.installed)
</script>

<template>
  <div class="setup">
    <header class="t-head">
      <div>
        <h2 class="content-title">Cài tự động</h2>
        <p class="secondary">Tải và cài ComfyUI, model AI, Blender, Claude Code vào thư mục bạn chọn. Đứt mạng thì bấm cài lại, phần đã tải được giữ nguyên.</p>
      </div>
    </header>

    <p v-if="!conn.transport" class="secondary">Core chưa chạy.</p>
    <p v-if="error" class="err" role="alert">{{ error }}</p>

    <!-- progress -->
    <template v-if="job && !showForm">
      <div class="card prog">
        <div class="p-top">
          <span class="headline">
            {{ job.state === 'running' ? 'Đang cài…' : job.state === 'done' ? 'Cài đặt xong' : job.state === 'cancelled' ? 'Đã huỷ' : 'Có lỗi' }}
          </span>
          <span class="caption secondary">
            {{ bytes(job.bytes_done) }} / {{ bytes(job.bytes_total) }}
            <template v-if="job.state === 'running' && job.speed"> · {{ bytes(job.speed) }}/s</template>
            <template v-if="eta"> · còn {{ eta }}</template>
          </span>
        </div>
        <div class="bar" role="progressbar" :aria-valuenow="Math.round(pct)" aria-valuemin="0" aria-valuemax="100">
          <span :style="{ width: `${pct}%` }" :class="job.state" />
        </div>
        <p v-if="job.error" class="err">{{ job.error }}</p>
      </div>

      <ol class="card items">
        <li v-for="it in job.items" :key="it.id" class="item" :class="it.state">
          <span class="ic">
            <LoaderCircle v-if="it.state === 'running'" :size="16" class="spin" />
            <CircleCheck v-else-if="it.state === 'done' || it.state === 'skipped'" :size="16" />
            <CircleX v-else-if="it.state === 'error'" :size="16" />
            <Ban v-else-if="it.state === 'cancelled'" :size="16" />
            <span v-else class="dot" />
          </span>
          <span class="it-text">
            <span class="it-line">
              <span class="it-label">{{ it.label }}</span>
              <span class="caption secondary">
                {{ STATE_TEXT[it.state] }}<template v-if="it.total && it.state !== 'skipped'"> · {{ it.done ? `${bytes(it.done)} / ${bytes(it.total)} · ${Math.floor((100 * it.done) / it.total)}%` : bytes(it.total) }}</template>
              </span>
            </span>
            <span v-if="it.detail" class="caption detail mono">{{ it.detail }}</span>
            <span v-if="it.total && it.state !== 'skipped'" class="bar thin"><span :class="it.state" :style="{ width: `${Math.min(100, (100 * it.done) / it.total)}%` }" /></span>
          </span>
        </li>
      </ol>

      <div class="actions">
        <button v-if="job.state === 'running'" class="btn" @click="cancel"><Ban /> Huỷ</button>
        <template v-else>
          <button v-if="job.state !== 'done'" class="btn btn-primary" @click="start"><RotateCw /> Cài tiếp</button>
          <button v-if="claudeReady" class="btn" :class="{ 'btn-primary': job.state === 'done' }" @click="claudeLogin"><LogIn /> Đăng nhập Claude</button>
          <button class="btn" @click="router.push({ path: '/settings', query: { pane: 'tools' } })">Xem đường dẫn công cụ</button>
          <button class="btn btn-plain" @click="showForm = true">Chọn lại gói</button>
        </template>
      </div>
    </template>

    <!-- form -->
    <template v-else-if="plan">
      <div class="card gpu" :class="{ bad: plan.gpu.problem }">
        <CircleAlert v-if="plan.gpu.problem" :size="16" />
        <CircleCheck v-else :size="16" />
        <span v-if="plan.gpu.problem">{{ plan.gpu.problem }}</span>
        <span v-else>{{ plan.gpu.name }} · {{ plan.gpu.vram_gb }} GB VRAM · driver {{ plan.gpu.driver }}</span>
      </div>

      <section class="card rows">
        <div class="row">
          <label for="setup-tools" class="lbl">Thư mục công cụ</label>
          <div class="val">
            <div class="input-row">
              <input id="setup-tools" v-model="toolsDir" class="field mono" spellcheck="false" @change="load({ tools_dir: toolsDir, models_dir: modelsDir })" />
              <button v-if="canPick" type="button" class="btn pick" @click="pick('tools')"><FolderOpen /> Chọn…</button>
            </div>
            <p class="caption secondary">ComfyUI, Python, Blender. Còn trống {{ plan.free.tools === null ? '?' : bytes(plan.free.tools) }}.</p>
          </div>
        </div>
        <div class="row">
          <label for="setup-models" class="lbl">Thư mục model</label>
          <div class="val">
            <div class="input-row">
              <input id="setup-models" v-model="modelsDir" class="field mono" spellcheck="false" @change="load({ tools_dir: toolsDir, models_dir: modelsDir })" />
              <button v-if="canPick" type="button" class="btn pick" @click="pick('models')"><FolderOpen /> Chọn…</button>
            </div>
            <p class="caption secondary">Nên để ổ SSD. Đã có model ở chỗ khác thì chọn thư mục đó, file trùng sẽ không tải lại. Còn trống {{ plan.free.models === null ? '?' : bytes(plan.free.models) }}.</p>
          </div>
        </div>
      </section>

      <section>
        <p class="section-label">Gói model</p>
        <div class="packs">
          <button
            v-for="p in plan.packs"
            :key="p.id"
            type="button"
            class="card pack"
            role="checkbox"
            :aria-checked="p.required || packs.has(p.id)"
            :aria-disabled="p.required"
            :class="{ on: p.required || packs.has(p.id), req: p.required }"
            @click="togglePack(p.id, p.required)"
          >
            <span class="check"><CircleCheck v-if="p.required || packs.has(p.id)" :size="16" /><span v-else class="dot" /></span>
            <span class="pk-text">
              <span class="pk-line">
                <span class="headline">{{ p.label }}</span>
                <span class="caption secondary">{{ p.installed ? `Đã có · ${bytes(p.size)}` : p.missing_size < p.size ? `còn ${bytes(p.missing_size)} / ${bytes(p.size)}` : bytes(p.size) }}</span>
              </span>
              <span class="caption secondary">{{ p.detail }}<template v-if="p.required"> Bắt buộc.</template></span>
            </span>
          </button>
        </div>
      </section>

      <section>
        <p class="section-label">Công cụ</p>
        <div class="card rows">
          <div class="tool">
            <span class="t-left"><CircleCheck v-if="plan.components.comfyui.installed" :size="15" class="ok" /><Download v-else :size="15" class="todo" /> <span class="headline">ComfyUI {{ plan.components.comfyui.version }}</span></span>
            <span class="caption secondary">{{ plan.components.comfyui.installed ? `Đã có · ${plan.components.comfyui.path}` : `Sẽ cài · khoảng ${bytes(plan.components.comfyui.size ?? 0)}` }}</span>
          </div>
          <label class="tool">
            <span class="t-left"><CircleCheck v-if="plan.components.blender.installed" :size="15" class="ok" /><input v-else v-model="blender" type="checkbox" /> <span class="headline">Blender {{ plan.components.blender.version }}</span></span>
            <span class="caption secondary">{{ plan.components.blender.installed ? `Đã có · ${plan.components.blender.path}` : 'Cho quy trình 3D và sprite qua mesh' }}</span>
          </label>
          <label class="tool">
            <span class="t-left"><CircleCheck v-if="plan.components.claude.installed" :size="15" class="ok" /><input v-else v-model="claude" type="checkbox" /> <span class="headline">Claude Code</span></span>
            <span class="caption secondary">{{ plan.components.claude.installed ? 'Đã có' : 'Cho các bước Claude viết brief, soát khớp xương. Cần tài khoản Claude' }}</span>
          </label>
        </div>
      </section>

      <p v-if="spaceProblem" class="err">{{ spaceProblem }}</p>
      <div class="go">
        <p class="secondary">
          <template v-if="nothingToDo">Mọi thứ đã có. Bấm để lưu lại đường dẫn.</template>
          <template v-else>Tải khoảng <b>{{ bytes(download.models + download.tools) }}</b>. Có thể dùng máy bình thường trong lúc tải.</template>
        </p>
        <div class="actions">
          <button v-if="job" class="btn btn-plain" @click="showForm = false">Xem lần cài trước</button>
          <button class="btn btn-primary big" :disabled="starting || loading || !!spaceProblem || !!plan.gpu.problem && !plan.components.comfyui.installed" @click="start">
            <LoaderCircle v-if="starting" class="spin" />
            <Download v-else />
            Cài tất cả
          </button>
        </div>
      </div>
    </template>
    <p v-else-if="loading" class="caption secondary">Đang kiểm tra máy…</p>
  </div>
</template>

<style scoped>
.setup {
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
.t-head .secondary {
  margin-top: 2px;
}
.err {
  color: var(--st-fail);
  font-size: var(--fs-callout);
}
.gpu {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  color: var(--st-ok);
}
.gpu span {
  color: var(--text);
}
.gpu.bad {
  color: var(--st-fail);
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
.row + .row,
.tool + .tool {
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
}
.input-row .field {
  flex: 1;
  min-width: 0;
}
.mono {
  font-family: var(--mono);
}
.field.mono {
  font-size: 12.5px;
}
.pick {
  flex: none;
  height: var(--ctl-h);
}
.section-label {
  margin-bottom: 8px;
}
.packs {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 10px;
}
.pack {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px 14px;
  text-align: left;
  font: inherit;
  color: inherit;
  cursor: pointer;
  transition:
    box-shadow var(--t-fast),
    background-color var(--t-fast);
}
.pack.on {
  box-shadow: inset 0 0 0 1.5px var(--accent);
}
.pack.req {
  cursor: default;
}
.check {
  flex: none;
  padding-top: 1px;
  color: var(--accent);
}
.dot {
  display: inline-block;
  width: 14px;
  height: 14px;
  margin: 1px;
  border-radius: 50%;
  box-shadow: inset 0 0 0 1.5px var(--separator-strong);
}
.pk-text {
  display: grid;
  gap: 3px;
  min-width: 0;
}
.pk-line,
.it-line,
.p-top {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 10px;
}
.tool {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 11px 0;
}
.tool .caption {
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.t-left {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}
.ok {
  color: var(--st-ok);
}
.todo {
  color: var(--accent);
}
.go {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.big {
  height: 34px;
  padding: 0 18px;
}
.prog {
  display: grid;
  gap: 10px;
  padding: 14px 18px;
}
.bar {
  display: block;
  height: 8px;
  border-radius: 4px;
  background: var(--fill-2, var(--separator));
  overflow: hidden;
}
.bar > span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--accent);
  transition: width 0.8s var(--ease-out);
}
.bar > span.done {
  background: var(--st-ok);
}
.bar > span.error {
  background: var(--st-fail);
}
.bar.thin {
  height: 4px;
  margin-top: 4px;
}
.items {
  list-style: none;
  margin: 0;
  padding: 4px 18px;
}
.item {
  display: flex;
  gap: 10px;
  padding: 11px 0;
}
.item + .item {
  border-top: 0.5px solid var(--separator);
}
.ic {
  flex: none;
  padding-top: 1px;
  color: var(--text-3);
}
.item.running .ic {
  color: var(--accent);
}
.item.done .ic,
.item.skipped .ic {
  color: var(--st-ok);
}
.item.error .ic {
  color: var(--st-fail);
}
.it-text {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
}
.it-label {
  font-weight: 500;
}
.detail {
  color: var(--text-2);
  font-size: 11.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item.error .detail {
  color: var(--st-fail);
  white-space: normal;
}
.spin {
  animation: spin 1s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .setup {
    animation: none;
  }
  .bar > span {
    transition: none;
  }
}
</style>

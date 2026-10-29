<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Check, CircleAlert, Copy, Download, LoaderCircle, RefreshCw, TerminalSquare } from '@lucide/vue'
import type { AgentInstallResult, AgentStatus, AgentTarget, McpInfo } from '../../../preload/types'
import { useToasts } from '@/stores/toasts'

const toasts = useToasts()
const info = ref<McpInfo | null>(null)
const status = ref<AgentStatus | null>(null)
const manual = ref<{ claude: string[]; codexToml: string } | null>(null)
const target = ref<AgentTarget>('claude')
const copied = ref<AgentTarget | null>(null)
const installing = ref<AgentTarget | null>(null)
const result = ref<Partial<Record<AgentTarget, AgentInstallResult>>>({})
const showManual = ref(false)
let resetTimer = 0

async function refresh(): Promise<void> {
  try {
    status.value = (await window.studio?.agentStatus()) ?? null
  } catch {
    status.value = null
  }
}

onMounted(async () => {
  try {
    info.value = (await window.studio?.mcpInfo()) ?? null
  } catch {
    info.value = null
  }
  await refresh()
})

const quote = (p: string): string => (/\s/.test(p) ? `"${p}"` : p)
const mcpOnly = computed(() => `claude mcp add -s user asset-studio -- ${[info.value?.command ?? '', ...(info.value?.args ?? [])].map(quote).join(' ')}`)

const snippet = computed(() => {
  if (!manual.value) return ''
  return target.value === 'claude'
    ? [...manual.value.claude, '', '# hoặc chỉ MCP (không skill / lệnh /asset-studio:…):', mcpOnly.value].join('\n')
    : `# ~/.codex/config.toml\n${manual.value.codexToml}`
})

const current = computed(() => status.value?.[target.value] ?? null)
const name = computed(() => (target.value === 'claude' ? 'Claude Code' : 'Codex CLI'))
const state = computed(() => {
  const s = current.value
  if (!s) return { tone: 'muted', text: 'Đang kiểm tra…' }
  if (s.mode === 'plugin') return { tone: 'ok', text: 'Đã cài plugin (MCP + skill + lệnh /asset-studio:make)' }
  if (s.installed && target.value === 'codex') return { tone: 'ok', text: s.skill ? 'Đã cài MCP + skill' : 'Đã có MCP, thiếu skill' }
  if (s.installed) return { tone: 'warn', text: 'Đã có MCP thủ công; cài plugin để có skill + lệnh (thay mục thủ công)' }
  if (!s.cli) return { tone: 'muted', text: `Chưa thấy lệnh ${target.value} trong PATH` }
  return { tone: 'muted', text: 'Chưa kết nối' }
})
const canInstall = computed(() => !!current.value && (target.value === 'codex' || !!current.value.cli) && current.value.mode !== 'plugin')
const installLabel = computed(() => (current.value?.installed && target.value === 'codex' ? 'Cài lại' : 'Cài 1 chạm'))

async function install(): Promise<void> {
  const t = target.value
  if (installing.value) return
  installing.value = t
  try {
    const r = await window.studio!.agentInstall(t)
    result.value = { ...result.value, [t]: r }
    if (r.ok) toasts.push(`Đã kết nối ${name.value}. Mở phiên mới để dùng.`, 'success', 3200)
    else toasts.push(`Cài ${name.value} chưa xong, xem nhật ký bên dưới`, 'error', 4000)
  } catch (e) {
    toasts.error(e, `Không cài được ${name.value}`)
  } finally {
    installing.value = null
    await refresh()
  }
}

async function toggleManual(): Promise<void> {
  showManual.value = !showManual.value
  if (showManual.value && !manual.value) {
    try {
      manual.value = (await window.studio?.agentCommands()) ?? null
    } catch (e) {
      toasts.error(e, 'Không chuẩn bị được lệnh')
    }
  }
}

async function copy(): Promise<void> {
  const t = target.value
  try {
    await window.studio?.copyText(snippet.value)
    copied.value = t
    window.clearTimeout(resetTimer)
    resetTimer = window.setTimeout(() => (copied.value = null), 1800)
  } catch (e) {
    toasts.error(e, 'Không copy được')
  }
}
</script>

<template>
  <section v-if="info" class="agent card">
    <header>
      <span class="ic"><TerminalSquare :size="17" :stroke-width="2" /></span>
      <div class="names">
        <h3 class="headline">Kết nối agent CLI</h3>
        <p class="caption">Claude Code / Codex CLI tự chạy quy trình, duyệt bước chờ, xuất vào Unity/Godot qua MCP. Mọi thao tác hiện trực tiếp ở thanh bên.</p>
      </div>
      <div class="seg" role="tablist" aria-label="Chọn agent CLI">
        <button
          v-for="t in (['claude', 'codex'] as const)"
          :key="t"
          role="tab"
          :aria-selected="target === t"
          :class="{ on: target === t }"
          @click="target = t"
        >
          {{ t === 'claude' ? 'Claude Code' : 'Codex CLI' }}
        </button>
      </div>
    </header>

    <div class="row">
      <span class="state" :class="state.tone" role="status">
        <Transition name="fade" mode="out-in">
          <Check v-if="state.tone === 'ok'" key="ok" :size="14" />
          <CircleAlert v-else key="no" :size="14" />
        </Transition>
        <Transition name="fade" mode="out-in">
          <span :key="state.text">{{ state.text }}</span>
        </Transition>
      </span>
      <button class="btn btn-plain" aria-label="Kiểm tra lại" title="Kiểm tra lại" @click="refresh"><RefreshCw :size="14" /></button>
      <button class="btn btn-primary" :disabled="!canInstall || !!installing" @click="install">
        <LoaderCircle v-if="installing === target" class="spin" :size="14" />
        <Download v-else :size="14" />
        {{ installing === target ? 'Đang cài…' : installLabel }}
      </button>
    </div>
    <p v-if="current?.detail" class="caption secondary mono detail">{{ current.detail }}</p>

    <Transition name="fade">
      <div v-if="result[target]" class="log" :class="{ bad: !result[target]!.ok }">
        <pre class="code selectable">{{ result[target]!.log.join('\n') }}</pre>
        <p v-if="result[target]!.backups.length" class="caption secondary">Bản sao cấu hình cũ: {{ result[target]!.backups.join(', ') }}</p>
      </div>
    </Transition>

    <button class="link-btn caption" :aria-expanded="showManual" @click="toggleManual">{{ showManual ? 'Ẩn lệnh tay' : 'Tự chạy lệnh tay' }}</button>
    <Transition name="fade">
      <div v-if="showManual && snippet" class="code-wrap">
        <Transition name="fade" mode="out-in">
          <pre :key="target" class="code selectable">{{ snippet }}</pre>
        </Transition>
        <button class="btn copy" :aria-label="`Copy lệnh kết nối ${target}`" @click="copy">
          <Transition name="fade" mode="out-in">
            <Check v-if="copied === target" key="ok" class="ok" />
            <Copy v-else key="copy" />
          </Transition>
          {{ copied === target ? 'Đã copy' : 'Copy' }}
        </button>
      </div>
    </Transition>
    <p class="caption secondary">
      Cài 1 chạm sao lưu cấu hình trước khi sửa. Gate mặc định chờ bạn duyệt trong app; agent chỉ tự chọn khi bạn bảo.
    </p>
  </section>
</template>

<style scoped>
.agent {
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
.row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.state {
  flex: 1;
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--fs-caption);
  color: var(--text-2);
}
.state.ok {
  color: var(--st-ok);
}
.state.warn {
  color: var(--st-waiting);
}
.detail {
  margin: -4px 0 0;
  word-break: break-all;
}
.code-wrap {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.code {
  flex: 1;
  min-width: 0;
  margin: 0;
  padding: 10px 12px;
  border-radius: var(--r-md);
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-all;
}
.log {
  display: grid;
  gap: 4px;
}
.log.bad .code {
  box-shadow: inset 0 0 0 1px var(--st-fail);
}
.link-btn {
  justify-self: start;
  padding: 0;
  border: 0;
  background: none;
  color: var(--accent);
  cursor: pointer;
}
.copy {
  flex: none;
}
.spin {
  animation: spin 0.9s linear infinite;
}
.ok {
  color: var(--st-ok);
}
@media (prefers-reduced-motion: reduce) {
  .seg button {
    transition: none;
  }
  .spin {
    animation: none;
  }
}
</style>

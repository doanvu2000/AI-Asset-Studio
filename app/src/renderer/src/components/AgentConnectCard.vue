<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Check, Copy, TerminalSquare } from '@lucide/vue'
import type { McpInfo } from '../../../preload/types'
import { useToasts } from '@/stores/toasts'

type Target = 'claude' | 'codex'

const toasts = useToasts()
const info = ref<McpInfo | null>(null)
const target = ref<Target>('claude')
const copied = ref<Target | null>(null)
let resetTimer = 0

onMounted(async () => {
  try {
    info.value = (await window.studio?.mcpInfo()) ?? null
  } catch {
    info.value = null
  }
})

const quote = (p: string): string => (/\s/.test(p) ? `"${p}"` : p)

const snippets = computed<Record<Target, string>>(() => {
  const cmd = info.value?.command ?? ''
  const args = info.value?.args ?? []
  return {
    claude: `claude mcp add -s user asset-studio -- ${[cmd, ...args].map(quote).join(' ')}`,
    // TOML literal strings keep Windows backslashes as-is; Codex cuts tool calls at 60s unless raised and
    // asks approval for every non-read-only tool (codex exec cannot answer) unless approved here.
    codex: [
      '[mcp_servers.asset-studio]',
      `command = '${cmd}'`,
      `args = ${JSON.stringify(args).replace(/,/g, ', ')}`,
      'tool_timeout_sec = 900',
      'default_tools_approval_mode = "approve"'
    ].join('\n')
  }
})

const hint = computed(() =>
  target.value === 'claude'
    ? 'Chạy lệnh này trong terminal. Claude Code sẽ dùng được Asset Studio ở mọi thư mục.'
    : 'Dán vào ~/.codex/config.toml rồi mở lại Codex CLI.'
)

async function copy(): Promise<void> {
  const t = target.value
  try {
    await window.studio?.copyText(snippets.value[t])
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
        <p class="caption">Claude Code / Codex CLI chạy quy trình, duyệt bước chờ và nhận file output qua MCP.</p>
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
    <div class="code-wrap">
      <Transition name="fade" mode="out-in">
        <pre :key="target" class="code selectable">{{ snippets[target] }}</pre>
      </Transition>
      <button class="btn copy" :aria-label="`Copy lệnh kết nối ${target}`" @click="copy">
        <Transition name="fade" mode="out-in">
          <Check v-if="copied === target" key="ok" class="ok" />
          <Copy v-else key="copy" />
        </Transition>
        {{ copied === target ? 'Đã copy' : 'Copy lệnh kết nối' }}
      </button>
    </div>
    <p class="caption secondary">{{ hint }} Gate mặc định chờ bạn duyệt trong app.</p>
  </section>
</template>

<style scoped>
.agent {
  display: grid;
  gap: 12px;
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
.seg {
  display: inline-flex;
  padding: 2px;
  border-radius: var(--r-sm);
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.seg button {
  height: 26px;
  padding: 0 10px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--text-2);
  font-size: var(--fs-callout);
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color var(--t-fast) var(--ease-out),
    color var(--t-fast);
}
.seg button.on {
  background: var(--bg-elevated);
  color: var(--text);
  box-shadow: var(--shadow-1);
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
.copy {
  flex: none;
}
.ok {
  color: var(--st-ok);
}
@media (prefers-reduced-motion: reduce) {
  .seg button {
    transition: none;
  }
}
</style>

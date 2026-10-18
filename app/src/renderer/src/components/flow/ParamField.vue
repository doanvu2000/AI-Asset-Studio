<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Link2, Unlink } from '@lucide/vue'
import type { ParamSpec } from '@/api/types'
import PresetSelect from '../PresetSelect.vue'

const props = defineProps<{ name: string; spec?: ParamSpec; value: unknown; inputs: string[]; bare?: boolean }>()
const emit = defineEmits<{ change: [v: unknown] }>()

const t = computed(() => props.spec?.type ?? guessType(props.value))
const bound = computed(() => typeof props.value === 'string' && props.value.includes('${'))
const fid = computed(() => `p-${props.name.replace(/[^\w-]/g, '_')}`)
const def = computed(() => (props.spec?.default === undefined || props.spec.default === '' ? '' : String(props.spec.default)))

function guessType(v: unknown): string {
  if (typeof v === 'number') return Number.isInteger(v) ? 'int' : 'float'
  if (typeof v === 'boolean') return 'bool'
  if (typeof v === 'string' || v === undefined) return 'text'
  return 'json'
}

const jsonText = ref('')
const jsonError = ref<string | null>(null)
watch(
  () => props.value,
  (v) => {
    jsonText.value = v === undefined ? '' : JSON.stringify(v, null, 1)
    jsonError.value = null
  },
  { immediate: true }
)

function commitJson(): void {
  const s = jsonText.value.trim()
  if (!s) return emit('change', undefined)
  try {
    emit('change', JSON.parse(s))
    jsonError.value = null
  } catch {
    jsonError.value = 'JSON không hợp lệ'
  }
}

function setNumber(raw: string): void {
  if (raw.trim() === '') return emit('change', undefined)
  const n = Number(raw)
  if (!Number.isNaN(n)) emit('change', t.value === 'int' ? Math.round(n) : n)
}

const bindOpen = ref(false)
function bindTo(input: string): void {
  bindOpen.value = false
  emit('change', `\${inputs.${input}}`)
}
</script>

<template>
  <div class="pf">
    <div v-if="!bare" class="pf-head">
      <label :for="fid" class="pf-label" :title="name">{{ spec?.label ?? name }}</label>
      <span v-if="spec?.label" class="pf-key">{{ name }}</span>
      <span class="sp" />
      <button v-if="bound" type="button" class="mini" title="Bỏ liên kết, nhập giá trị cố định" @click="emit('change', undefined)"><Unlink :size="12" /></button>
      <div v-else-if="inputs.length && t !== 'json' && t !== 'list'" class="bind">
        <button type="button" class="mini" title="Lấy từ đầu vào recipe" :aria-expanded="bindOpen" @click="bindOpen = !bindOpen"><Link2 :size="12" /></button>
        <Transition name="pop">
          <ul v-if="bindOpen" class="menu" role="menu">
            <li v-for="i in inputs" :key="i"><button type="button" role="menuitem" @click="bindTo(i)">inputs.{{ i }}</button></li>
          </ul>
        </Transition>
      </div>
    </div>

    <input v-if="bound" :id="fid" class="field mono" :value="String(value)" @change="emit('change', ($event.target as HTMLInputElement).value)" />
    <PresetSelect v-else-if="t === 'preset'" :id="fid" :model-value="value as string | undefined" @update:model-value="emit('change', $event)" />
    <div v-else-if="(t === 'int' || t === 'float') && spec?.options?.length" class="seg">
      <button
        v-for="o in spec.options"
        :key="String(o)"
        type="button"
        class="num"
        :class="{ on: value === o || (value === undefined && spec.default === o) }"
        @click="emit('change', o)"
      >
        {{ o }}
      </button>
    </div>
    <input
      v-else-if="t === 'int' || t === 'float'"
      :id="fid"
      class="field num"
      type="number"
      :min="spec?.min"
      :max="spec?.max"
      :step="t === 'int' ? 1 : 0.01"
      :placeholder="def ? `Mặc định ${def}` : ''"
      :value="value === undefined ? '' : String(value)"
      @change="setNumber(($event.target as HTMLInputElement).value)"
    />
    <label v-else-if="t === 'bool'" class="sw">
      <input :id="fid" type="checkbox" role="switch" :checked="value === undefined ? spec?.default === true : value === true" @change="emit('change', ($event.target as HTMLInputElement).checked)" />
      <span class="track"><span class="knob" /></span>
      <span v-if="value === undefined" class="caption">mặc định</span>
    </label>
    <select v-else-if="t === 'enum'" :id="fid" class="field" :value="value === undefined ? '' : String(value)" @change="emit('change', ($event.target as HTMLSelectElement).value || undefined)">
      <option value="">{{ def ? `Mặc định (${def})` : '—' }}</option>
      <option v-for="o in spec?.options ?? []" :key="String(o)" :value="String(o)">{{ o === '' ? '(trống)' : o }}</option>
    </select>
    <template v-else-if="t === 'json' || t === 'list'">
      <textarea :id="fid" v-model="jsonText" class="field mono" rows="3" spellcheck="false" placeholder="JSON" @blur="commitJson" />
      <p v-if="jsonError" class="err">{{ jsonError }}</p>
    </template>
    <textarea
      v-else-if="spec?.multiline"
      :id="fid"
      class="field"
      :class="{ mono: spec.code }"
      :rows="spec.code ? 6 : 3"
      :placeholder="def"
      :value="value === undefined ? '' : String(value)"
      @change="emit('change', ($event.target as HTMLTextAreaElement).value || undefined)"
    />
    <input v-else :id="fid" class="field" :placeholder="def" :value="value === undefined ? '' : String(value)" @change="emit('change', ($event.target as HTMLInputElement).value || undefined)" />
  </div>
</template>

<style scoped>
.pf {
  display: grid;
  gap: 5px;
}
.pf-head {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 20px;
}
.pf-label {
  font-weight: 500;
  color: var(--text-2);
  font-size: var(--fs-callout);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pf-key {
  font-family: var(--mono);
  font-size: 10px;
  color: var(--text-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sp {
  flex: 1;
}
.mini {
  display: grid;
  place-items: center;
  width: 22px;
  height: 20px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--text-3);
  cursor: pointer;
  transition: background-color var(--t-fast);
}
.mini:hover {
  background: var(--bg-hover);
  color: var(--accent);
}
.bind {
  position: relative;
}
.menu {
  position: absolute;
  right: 0;
  top: 24px;
  z-index: 20;
  list-style: none;
  margin: 0;
  padding: 4px;
  min-width: 160px;
  border-radius: 8px;
  background: var(--bg-elevated);
  box-shadow: var(--shadow-3), var(--hairline);
}
.menu button {
  width: 100%;
  text-align: left;
  border: 0;
  background: transparent;
  padding: 5px 8px;
  border-radius: 5px;
  font-family: var(--mono);
  font-size: 11.5px;
  cursor: pointer;
}
.menu button:hover {
  background: var(--accent-soft);
}
.field {
  font-size: var(--fs-body);
  min-height: 30px;
  padding: 5px 9px;
}
select.field {
  appearance: auto;
}
.mono {
  font-family: var(--mono);
  font-size: 11.5px;
}
.seg {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 2px;
  padding: 2px;
  border-radius: 8px;
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
  justify-self: start;
}
.seg button {
  min-width: 40px;
  height: 26px;
  padding: 0 9px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color var(--t-fast),
    box-shadow var(--t-fast);
}
.seg button.on {
  background: var(--bg-elevated);
  box-shadow: var(--shadow-1), var(--hairline);
  font-weight: 600;
}
.sw {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}
.sw input {
  position: absolute;
  opacity: 0;
  width: 1px;
  height: 1px;
}
.track {
  position: relative;
  width: 34px;
  height: 20px;
  border-radius: 10px;
  background: var(--bg-active);
  box-shadow: var(--hairline);
  transition: background-color var(--t-base) var(--ease-out);
}
.knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);
  transition: transform var(--t-base) var(--ease-spring);
}
.sw input:checked + .track {
  background: var(--st-ok);
}
.sw input:checked + .track .knob {
  transform: translateX(14px);
}
.sw input:focus-visible + .track {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}
.err {
  font-size: var(--fs-caption);
  color: var(--st-fail);
}
.pop-enter-active {
  transition:
    opacity var(--t-fast) var(--ease-out),
    transform var(--t-fast) var(--ease-spring);
}
.pop-leave-active {
  transition: opacity 90ms var(--ease-in);
}
.pop-enter-from {
  opacity: 0;
  transform: translateY(-4px) scale(0.98);
}
.pop-leave-to {
  opacity: 0;
}
</style>

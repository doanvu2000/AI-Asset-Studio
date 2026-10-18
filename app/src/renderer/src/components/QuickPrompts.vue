<script setup lang="ts">
import { computed, ref } from 'vue'
import { QUICK_STYLES, QUICK_SUBJECTS } from '@/assets/quickPrompts'

// In batch mode the text is one prompt per line: a subject chip appends a line and the style is one shared suffix
// the caller appends to every line at run time, so it never has to be found and rewritten inside each line.
const props = defineProps<{ modelValue: string; batch?: boolean; batchStyle?: string | null }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; 'update:batchStyle': [value: string | null] }>()

const subject = ref<string | null>(null)
const style = ref<string | null>(null)
const activeStyle = computed(() => (props.batch ? (props.batchStyle ?? null) : style.value))

// Chips edit only their own part: the subject is the leading clause, the style the trailing one, so text the
// user typed in between survives switching chips.
function compose(nextSubject: string | null, nextStyle: string | null): string {
  let text = props.modelValue.trim()
  if (style.value && text.endsWith(style.value)) text = text.slice(0, -style.value.length).replace(/[,\s]+$/, '')
  if (subject.value && text.startsWith(subject.value)) text = text.slice(subject.value.length).replace(/^[,\s]+/, '')
  return [nextSubject, text, nextStyle].filter(Boolean).join(', ')
}

function pickSubject(text: string): void {
  if (props.batch) {
    const cur = props.modelValue.replace(/\s+$/, '')
    emit('update:modelValue', cur ? `${cur}\n${text}` : text)
    return
  }
  const next = subject.value === text ? null : text
  emit('update:modelValue', compose(next, style.value))
  subject.value = next
}

function pickStyle(text: string): void {
  const next = activeStyle.value === text ? null : text
  if (props.batch) {
    emit('update:batchStyle', next)
    return
  }
  emit('update:modelValue', compose(subject.value, next))
  style.value = next
}
</script>

<template>
  <div class="qp">
    <div class="row" role="group" :aria-label="batch ? 'Thêm dòng theo chủ đề' : 'Chủ đề mẫu'">
      <span class="caption lbl">{{ batch ? 'Thêm dòng' : 'Chủ đề' }}</span>
      <div class="chips">
      <button
        v-for="[vi, en] in QUICK_SUBJECTS"
        :key="vi"
        type="button"
        class="chip"
        :class="{ on: !batch && subject === en }"
        :aria-pressed="batch ? undefined : subject === en"
        :title="en"
        @click="pickSubject(en)"
      >
        {{ vi }}
      </button>
      </div>
    </div>
    <div class="row" role="group" :aria-label="batch ? 'Phong cách cho cả lô' : 'Phong cách mẫu'">
      <span class="caption lbl">{{ batch ? 'Cả lô' : 'Phong cách' }}</span>
      <div class="chips">
      <button
        v-for="[vi, en] in QUICK_STYLES"
        :key="vi"
        type="button"
        class="chip"
        :class="{ on: activeStyle === en }"
        :aria-pressed="activeStyle === en"
        :title="en"
        @click="pickStyle(en)"
      >
        {{ vi }}
      </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.qp {
  display: grid;
  gap: 6px;
  margin-bottom: 8px;
}
.row {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  align-items: start;
  gap: 4px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.lbl {
  padding-top: 5px;
  color: var(--text-3);
}
.chip {
  height: 26px;
  padding: 0 10px;
  border: 0;
  border-radius: 999px;
  font-size: var(--fs-callout);
  color: var(--text-2);
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
  cursor: pointer;
  transition:
    background-color var(--t-fast),
    color var(--t-fast),
    box-shadow var(--t-fast);
}
.chip:hover {
  color: var(--text);
}
.chip.on {
  color: var(--accent);
  background: var(--accent-soft);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 40%, transparent);
  font-weight: 600;
}
</style>

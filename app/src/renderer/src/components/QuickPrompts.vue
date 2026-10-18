<script setup lang="ts">
import { ref } from 'vue'

// Labels are Vietnamese for the user; the text sent to the models stays English, which they follow far better.
const SUBJECTS: ReadonlyArray<readonly [string, string]> = [
  ['Cây cối', 'a stylized tree with lush foliage, whole tree from roots to crown'],
  ['Bụi cỏ & hoa', 'a small bush with flowers and grass tufts'],
  ['Đá', 'a cluster of mossy rocks and boulders'],
  ['Pha lê', 'a glowing crystal cluster ore deposit'],
  ['Nhà cửa', 'a small house with roof, door and windows'],
  ['Vũ khí', 'a single ornate sword'],
  ['Rương báu', 'a treasure chest'],
  ['Bình thuốc', 'a potion bottle with glowing liquid'],
  ['Thùng & hộp', 'a wooden barrel and crate prop'],
  ['Nhân vật', 'a full-body hero character standing'],
  ['Quái vật', 'a monster creature, full body'],
  ['Linh thú', 'a mythical beast companion, full body']
]
const STYLES: ReadonlyArray<readonly [string, string]> = [
  ['Trung cổ', 'medieval fantasy style, weathered wood, iron and stone, earthy colors'],
  ['Anime', 'anime style, cel shading, clean lineart, vibrant colors'],
  ['Tu tiên', 'xianxia cultivation fantasy style, ancient Chinese aesthetic, jade and gold, flowing silk, ethereal spiritual glow'],
  ['Nhật cổ', 'feudal Japan style, lacquered wood, paper and bronze details'],
  ['Chibi', 'chibi style, cute big-head proportions'],
  ['Hoạt hình', 'stylized hand-painted cartoon style of a mobile game'],
  ['Pixel art', 'pixel art style, crisp pixels, limited palette'],
  ['Low-poly', 'low-poly 3D style, flat shaded facets'],
  ['Dark fantasy', 'dark fantasy style, gothic, desaturated, ominous'],
  ['Sci-fi', 'sci-fi style, sleek metal with neon accents']
]

const props = defineProps<{ modelValue: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const subject = ref<string | null>(null)
const style = ref<string | null>(null)

// Chips edit only their own part: the subject is the leading clause, the style the trailing one, so text the
// user typed in between survives switching chips.
function compose(nextSubject: string | null, nextStyle: string | null): string {
  let text = props.modelValue.trim()
  if (style.value && text.endsWith(style.value)) text = text.slice(0, -style.value.length).replace(/[,\s]+$/, '')
  if (subject.value && text.startsWith(subject.value)) text = text.slice(subject.value.length).replace(/^[,\s]+/, '')
  return [nextSubject, text, nextStyle].filter(Boolean).join(', ')
}

function pickSubject(text: string): void {
  const next = subject.value === text ? null : text
  emit('update:modelValue', compose(next, style.value))
  subject.value = next
}

function pickStyle(text: string): void {
  const next = style.value === text ? null : text
  emit('update:modelValue', compose(subject.value, next))
  style.value = next
}
</script>

<template>
  <div class="qp">
    <div class="row" role="group" aria-label="Chủ đề mẫu">
      <span class="caption lbl">Chủ đề</span>
      <div class="chips">
      <button
        v-for="[vi, en] in SUBJECTS"
        :key="vi"
        type="button"
        class="chip"
        :class="{ on: subject === en }"
        :aria-pressed="subject === en"
        :title="en"
        @click="pickSubject(en)"
      >
        {{ vi }}
      </button>
      </div>
    </div>
    <div class="row" role="group" aria-label="Phong cách mẫu">
      <span class="caption lbl">Phong cách</span>
      <div class="chips">
      <button
        v-for="[vi, en] in STYLES"
        :key="vi"
        type="button"
        class="chip"
        :class="{ on: style === en }"
        :aria-pressed="style === en"
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

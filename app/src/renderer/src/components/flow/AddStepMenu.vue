<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { onClickOutside, onKeyStroke } from '@vueuse/core'
import { Plus, Search } from '@lucide/vue'
import type { KindInfo, Lane } from '@/api/types'
import { kindName, LANE_LABEL } from '@/composables/format'

const props = defineProps<{ kinds: KindInfo[] }>()
const emit = defineEmits<{ pick: [kind: string] }>()

const open = ref(false)
const q = ref('')
const root = ref<HTMLElement | null>(null)
const searchEl = ref<HTMLInputElement | null>(null)
onClickOutside(root, () => (open.value = false))
onKeyStroke('Escape', () => (open.value = false))

watch(open, async (o) => {
  if (!o) return
  q.value = ''
  await nextTick()
  searchEl.value?.focus()
})

const LANES: Lane[] = ['gpu', 'cloud', 'blender', 'cpu', 'none']
const title = (k: KindInfo): string => {
  const n = kindName(k.kind)
  return n === k.kind ? k.label : n
}
const groups = computed(() => {
  const term = q.value.trim().toLowerCase()
  const match = (k: KindInfo): boolean => !term || k.kind.toLowerCase().includes(term) || title(k).toLowerCase().includes(term)
  return LANES.map((lane) => ({ lane, items: props.kinds.filter((k) => k.lane === lane && match(k)) })).filter((g) => g.items.length)
})

function pick(kind: string): void {
  open.value = false
  emit('pick', kind)
}

function onEnter(): void {
  const first = groups.value[0]?.items[0]
  if (first) pick(first.kind)
}
</script>

<template>
  <div ref="root" class="add">
    <button type="button" class="btn" :aria-expanded="open" @click="open = !open"><Plus /> Thêm bước</button>
    <Transition name="pop">
      <div v-if="open" class="panel" role="dialog" aria-label="Chọn loại bước">
        <label class="search">
          <Search :size="14" />
          <input ref="searchEl" v-model="q" placeholder="Tìm loại bước…" @keydown.enter.prevent="onEnter" />
        </label>
        <div class="list">
          <section v-for="g in groups" :key="g.lane">
            <h4 class="section-label" :class="`lane-${g.lane}`"><span class="dot" />{{ LANE_LABEL[g.lane] }}</h4>
            <button v-for="k in g.items" :key="k.kind" type="button" class="item" @click="pick(k.kind)">
              <span class="it-name">{{ title(k) }}</span>
              <span class="it-kind">{{ k.kind }}</span>
            </button>
          </section>
          <p v-if="!groups.length" class="caption empty">Không có loại bước khớp</p>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.add {
  position: relative;
}
.panel {
  position: absolute;
  right: 0;
  top: 36px;
  z-index: 30;
  width: 320px;
  max-height: min(520px, 70vh);
  display: flex;
  flex-direction: column;
  border-radius: var(--r-lg);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-3), var(--hairline);
  overflow: hidden;
  transform-origin: top right;
}
.search {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 0.5px solid var(--separator);
  color: var(--text-3);
}
.search input {
  flex: 1;
  border: 0;
  background: transparent;
  outline: none;
  font-size: var(--fs-body);
  user-select: text;
}
.list {
  overflow: auto;
  padding: 6px;
}
section + section {
  margin-top: 6px;
}
h4 {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px 4px;
  margin: 0;
}
.dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--lane-cpu);
}
.lane-gpu .dot {
  background: var(--lane-gpu);
}
.lane-cloud .dot {
  background: var(--lane-cloud);
}
.lane-blender .dot {
  background: var(--lane-blender);
}
.lane-none .dot {
  background: var(--lane-none);
}
.item {
  display: grid;
  width: 100%;
  text-align: left;
  gap: 1px;
  padding: 6px 10px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  cursor: pointer;
  transition: background-color var(--t-fast);
}
.item:hover,
.item:focus-visible {
  background: var(--accent-soft);
}
.it-name {
  font-weight: 500;
}
.it-kind {
  font-family: var(--mono);
  font-size: 10.5px;
  color: var(--text-3);
}
.empty {
  padding: 16px;
  text-align: center;
}
.pop-enter-active {
  transition:
    opacity var(--t-fast) var(--ease-out),
    transform var(--t-base) var(--ease-spring);
}
.pop-leave-active {
  transition:
    opacity 110ms var(--ease-in),
    transform 110ms var(--ease-in);
}
.pop-enter-from,
.pop-leave-to {
  opacity: 0;
  transform: scale(0.96) translateY(-4px);
}
</style>

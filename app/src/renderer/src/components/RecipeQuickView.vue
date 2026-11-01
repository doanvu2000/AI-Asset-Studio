<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'
import { ImageOff, Pencil, Play, RotateCw, TriangleAlert, X } from '@lucide/vue'
import type { RecipeDetail, RecipeInfo } from '@/api/types'
import RecipeDemoImage from './RecipeDemoImage.vue'
import LaneTag from './LaneTag.vue'
import FlowCanvas from './flow/FlowCanvas.vue'
import { buildEditorGraph } from '@/composables/graph'
import { exportLabels, forgetRecipeDetail, recipeDemo, recipeDetail, recipeOutputs, requiredInputs, type RecipeDemo } from '@/composables/recipePeek'
import { useFocusTrap } from '@/composables/focusTrap'
import { useKinds } from '@/stores/catalog'

const props = defineProps<{ recipe: RecipeInfo | null }>()
const emit = defineEmits<{ close: []; run: [id: string]; edit: [id: string] }>()

const kinds = useKinds()
const root = ref<HTMLElement | null>(null)
const closeBtn = ref<HTMLButtonElement | null>(null)
const isOpen = computed(() => !!props.recipe)
const trap = useFocusTrap(root, isOpen, closeBtn)

const detail = ref<RecipeDetail | null>(null)
const demo = ref<RecipeDemo | null>(null)
const loading = ref(false)
const error = ref('')

async function load(r: RecipeInfo): Promise<void> {
  detail.value = null
  demo.value = null
  error.value = ''
  loading.value = true
  void kinds.load()
  try {
    const d = await recipeDetail(r.id)
    if (props.recipe?.id !== r.id) return
    detail.value = d
    const h = await recipeDemo(r)
    if (props.recipe?.id === r.id) demo.value = h
  } catch (e) {
    if (props.recipe?.id !== r.id) return
    forgetRecipeDetail(r.id)
    error.value = (e as Error).message
  } finally {
    if (props.recipe?.id === r.id) loading.value = false
  }
}
watch(
  () => props.recipe,
  (r) => r && void load(r),
  { immediate: true }
)

const graph = computed(() => (detail.value && kinds.loaded ? buildEditorGraph(detail.value.doc, kinds.byKind, [], null) : null))
const needs = computed(() => (props.recipe ? requiredInputs(props.recipe) : []))
const outputs = computed(() => (detail.value ? recipeOutputs(detail.value, kinds.byKind) : []))
const exportTags = computed(() => exportLabels(props.recipe))
const lanes = computed(() => [...new Set((props.recipe?.steps ?? []).map((s) => s.lane).filter((l) => l && l !== 'none'))])

useEventListener(document, 'keydown', (e: KeyboardEvent) => {
  if (!props.recipe) return
  if (trap.onTab(e)) return
  if (e.key === 'Escape') {
    e.preventDefault()
    emit('close')
  }
})
</script>

<template>
  <Teleport to="body">
    <Transition name="qv">
      <div v-if="recipe" ref="root" class="scrim" role="dialog" aria-modal="true" aria-labelledby="qv-title" @click.self="emit('close')">
        <section class="sheet">
          <header class="head">
            <div class="who">
              <h2 id="qv-title" class="headline">{{ recipe.name }}</h2>
              <span class="caption">{{ recipe.steps.length }} bước · {{ recipe.id }}</span>
            </div>
            <button type="button" class="btn btn-plain" @click="emit('edit', recipe.id)"><Pencil /> Sửa</button>
            <button type="button" class="btn btn-primary" @click="emit('run', recipe.id)"><Play /> Chạy</button>
            <button ref="closeBtn" type="button" class="btn btn-plain btn-icon" aria-label="Đóng (Esc)" title="Đóng (Esc)" @click="emit('close')"><X /></button>
          </header>
          <div class="body">
            <section class="flow" aria-label="Sơ đồ các bước">
              <FlowCanvas v-if="graph" :id="`qv-${recipe.id}`" :nodes="graph.nodes" :edges="graph.edges" :fit-key="recipe.id" :minimap="false" />
              <span v-else-if="error" class="caption state err"><TriangleAlert :size="14" /> {{ error }} <button type="button" class="btn btn-plain" @click="load(recipe)"><RotateCw /> Thử lại</button></span>
              <span v-else class="skel fill" />
            </section>
            <aside class="side">
              <p class="caption desc selectable">{{ recipe.description || 'Chưa có mô tả.' }}</p>
              <h3 class="section-label">Demo</h3>
              <div class="demo checker">
                <span v-if="loading" class="skel fill" />
                <RecipeDemoImage v-else-if="demo" :demo="demo" :full="true" :alt="`Kết quả mẫu của ${recipe.name}`" @failed="demo = null" />
                <span v-else class="caption empty"><ImageOff :size="16" /> Chưa có demo — chạy thử để tạo</span>
              </div>
              <h3 class="section-label">Cần</h3>
              <p class="tags">
                <span v-for="n in needs" :key="n.key" class="chip">{{ n.label }}<i v-if="n.type"> · {{ n.type }}</i></span>
                <span v-if="!needs.length" class="caption">Không bắt buộc đầu vào nào</span>
              </p>
              <h3 class="section-label">Ra</h3>
              <p class="tags">
                <span v-for="o in outputs" :key="o.name" class="chip">{{ o.name }}<i v-if="o.type"> · {{ o.type }}</i></span>
                <span v-if="!outputs.length" class="caption">{{ loading ? 'Đang tải…' : '—' }}</span>
              </p>
              <p class="tags meta">
                <LaneTag v-for="l in lanes" :key="l" :lane="l" />
                <span v-for="e in exportTags" :key="e" class="chip">Xuất {{ e }}</span>
                <span class="caption" :class="{ gpu: lanes.includes('gpu') }">{{ lanes.includes('gpu') ? 'Dùng GPU' : 'Không dùng GPU' }}</span>
              </p>
            </aside>
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.32);
}
.sheet {
  width: min(1040px, 100%);
  height: min(640px, 100%);
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  background: var(--bg-elevated);
  border-radius: var(--r-xl);
  box-shadow: var(--shadow-3);
  overflow: hidden;
}
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 10px 12px 16px;
  border-bottom: 0.5px solid var(--separator);
}
.who {
  flex: 1;
  min-width: 0;
  display: grid;
}
.body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  min-height: 0;
}
.flow {
  position: relative;
  min-height: 0;
  border-right: 0.5px solid var(--separator);
}
.side {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 14px;
  overflow-y: auto;
}
.side .section-label {
  margin: 8px 0 0;
}
.desc {
  margin: 0;
}
.demo {
  position: relative;
  display: grid;
  place-items: center;
  min-height: 170px;
  max-height: 240px;
  border-radius: var(--r-md);
  overflow: hidden;
  box-shadow: var(--hairline);
}
.demo :deep(img) {
  max-width: 100%;
  max-height: 240px;
}
.empty {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  color: var(--text-3);
  background: var(--bg-elevated);
  border-radius: var(--r-sm);
}
.tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 6px;
  margin: 0;
}
.chip {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  color: var(--text-2);
  background: var(--bg-sunken);
  box-shadow: var(--hairline);
}
.chip i {
  font-style: normal;
  color: var(--text-3);
}
.meta {
  margin-top: 8px;
}
.gpu {
  color: var(--st-waiting);
  font-weight: 600;
}
.state {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.err {
  color: var(--st-failed, var(--text-2));
}
.skel {
  display: block;
  background: linear-gradient(90deg, var(--bg-sunken) 25%, var(--bg-active) 50%, var(--bg-sunken) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.2s linear infinite;
}
.skel.fill {
  position: absolute;
  inset: 0;
}
@keyframes shimmer {
  to {
    background-position: -200% 0;
  }
}
@media (max-width: 820px) {
  .body {
    grid-template-columns: 1fr;
    grid-template-rows: minmax(220px, 1fr) auto;
    overflow-y: auto;
  }
  .flow {
    border-right: 0;
    border-bottom: 0.5px solid var(--separator);
  }
}
.qv-enter-active,
.qv-leave-active {
  transition: opacity var(--t-base) var(--ease-out);
}
.qv-enter-active .sheet {
  transition: transform var(--t-slow) var(--ease-spring);
}
.qv-leave-active .sheet {
  transition: transform var(--t-fast) var(--ease-in);
}
.qv-enter-from,
.qv-leave-to {
  opacity: 0;
}
.qv-enter-from .sheet,
.qv-leave-to .sheet {
  transform: scale(0.97);
}
@media (prefers-reduced-motion: reduce) {
  .qv-enter-active .sheet,
  .qv-leave-active .sheet,
  .skel {
    transition: none;
    animation: none;
  }
}
</style>

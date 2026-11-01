<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ImageOff } from '@lucide/vue'
import type { RecipeDetail, RecipeInfo } from '@/api/types'
import RecipeDemoImage from './RecipeDemoImage.vue'
import LaneTag from './LaneTag.vue'
import { exportLabels, recipeDemo, recipeDetail, recipeOutputs, requiredInputs, type RecipeDemo } from '@/composables/recipePeek'
import { useKinds } from '@/stores/catalog'

const props = defineProps<{ recipe: RecipeInfo; anchor: DOMRect }>()
const WIDTH = 330
const GAP = 10

const kinds = useKinds()
const detail = ref<RecipeDetail | null>(null)
const demo = ref<RecipeDemo | null>(null)
const demoDone = ref(false)
let alive = true
onBeforeUnmount(() => (alive = false))

onMounted(async () => {
  void kinds.load()
  recipeDemo(props.recipe)
    .then((d) => alive && (demo.value = d))
    .catch(() => undefined)
    .finally(() => alive && (demoDone.value = true))
  try {
    const d = await recipeDetail(props.recipe.id)
    if (alive) detail.value = d
  } catch {
    /* the summary falls back to the list data */
  }
})

const style = computed(() => {
  const a = props.anchor
  const right = a.right + GAP + WIDTH <= window.innerWidth - 8
  const left = right ? a.right + GAP : Math.max(8, a.left - GAP - WIDTH)
  const top = Math.min(Math.max(8, a.top), Math.max(8, window.innerHeight - 380))
  return { left: `${left}px`, top: `${top}px`, width: `${WIDTH}px` }
})
const needs = computed(() => requiredInputs(props.recipe))
const lanes = computed(() => [...new Set(props.recipe.steps.map((s) => s.lane).filter((l) => l && l !== 'none'))])
const usesGpu = computed(() => lanes.value.includes('gpu'))
const outputs = computed(() => (detail.value ? recipeOutputs(detail.value, kinds.byKind) : null))
const exportTags = computed(() => exportLabels(props.recipe))
</script>

<template>
  <div class="peek card" :style="style" role="tooltip">
    <h3 class="headline">{{ recipe.name }}</h3>
    <p class="caption desc">{{ recipe.description || 'Chưa có mô tả.' }}</p>
    <dl>
      <dt>Cần</dt>
      <dd>
        <template v-if="needs.length">
          <span v-for="n in needs.slice(0, 4)" :key="n.key" class="chip">{{ n.label }}<i v-if="n.type"> · {{ n.type }}</i></span>
          <span v-if="needs.length > 4" class="caption">+{{ needs.length - 4 }}</span>
        </template>
        <span v-else class="caption">Không bắt buộc đầu vào nào</span>
      </dd>
      <dt>Ra</dt>
      <dd>
        <span v-if="!outputs" class="skel w1" />
        <template v-else-if="outputs.length">
          <span v-for="o in outputs" :key="o.name" class="chip">{{ o.name }}<i v-if="o.type"> · {{ o.type }}</i></span>
        </template>
        <span v-else class="caption">—</span>
      </dd>
      <template v-if="exportTags.length">
        <dt>Xuất</dt>
        <dd><span v-for="e in exportTags" :key="e" class="chip">{{ e }}</span></dd>
      </template>
      <dt>Chạy</dt>
      <dd>
        <span class="caption num">{{ recipe.steps.length }} bước</span>
        <LaneTag v-for="l in lanes" :key="l" :lane="l" />
        <span class="caption" :class="{ gpu: usesGpu }">{{ usesGpu ? 'Dùng GPU' : 'Không dùng GPU' }}</span>
      </dd>
    </dl>
    <div class="demo checker">
      <span v-if="!demoDone" class="skel fill" />
      <RecipeDemoImage v-else-if="demo" :demo="demo" :size="320" :alt="`Kết quả mẫu của ${recipe.name}`" @failed="demo = null" />
      <span v-else class="caption empty"><ImageOff :size="16" /> Chưa có demo — chạy thử để tạo</span>
    </div>
  </div>
</template>

<style scoped>
.peek {
  position: fixed;
  z-index: 55;
  display: grid;
  gap: 8px;
  padding: 14px;
  background: var(--bg-elevated);
  box-shadow: var(--shadow-3), var(--hairline);
  pointer-events: none;
  transform-origin: left top;
}
.desc {
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
  margin: 0;
}
dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 10px;
  margin: 0;
  align-items: baseline;
}
dt {
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--text-3);
}
dd {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 6px;
  margin: 0;
  min-width: 0;
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
.gpu {
  color: var(--st-waiting);
  font-weight: 600;
}
.demo {
  position: relative;
  display: grid;
  place-items: center;
  grid-template: minmax(0, 1fr) / minmax(0, 1fr);
  height: 150px;
  border-radius: var(--r-md);
  overflow: hidden;
  box-shadow: var(--hairline);
}
.demo :deep(img) {
  max-width: 100%;
  max-height: 100%;
}
.empty {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 12px;
  text-align: center;
  color: var(--text-3);
  background: var(--bg-elevated);
  border-radius: var(--r-sm);
}
.skel {
  display: block;
  border-radius: var(--r-sm);
  background: linear-gradient(90deg, var(--bg-sunken) 25%, var(--bg-active) 50%, var(--bg-sunken) 75%);
  background-size: 200% 100%;
  animation: shimmer 1.2s linear infinite;
}
.skel.w1 {
  width: 120px;
  height: 14px;
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
@media (prefers-reduced-motion: reduce) {
  .skel {
    animation: none;
  }
}
</style>

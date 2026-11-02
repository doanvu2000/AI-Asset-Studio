<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Boxes, FileText } from '@lucide/vue'
import type { RecipeInfo, WorkflowDef } from '@/api/types'
import { useRecipes } from '@/stores/recipes'
import { useFlows } from '@/stores/flows'
import { useFit } from '@/stores/fit'
import { useUiMode } from '@/composables/uiMode'
import FitBadge from './FitBadge.vue'
import { recipeDemo, type RecipeDemo } from '@/composables/recipePeek'
import RecipeDemoImage from './RecipeDemoImage.vue'

type Goal = { id: string; title: string; text: string; flow?: string; recipes?: string[]; gdd?: boolean }

const GOALS: Goal[] = [
  { id: 'character', title: 'Nhân vật game', text: 'Từ một ý tưởng ra nhân vật biết đi, chạy, đánh, dùng được ngay trong game.', flow: 'character-pack' },
  { id: 'level', title: 'Màn chơi', text: 'Nền đất, đồ trang trí và phông xa ghép thành một màn chơi hoàn chỉnh.', flow: 'level-pack' },
  { id: 'ui', title: 'Bộ UI app/game', text: 'Nút, khung, thanh và icon cùng một phong cách cho cả ứng dụng.', flow: 'ui-pack' },
  { id: 'slice', title: 'Bản chơi thử đủ bộ', text: 'Một lần làm xong nhân vật, màn chơi và giao diện từ cùng một ý tưởng.', flow: 'game-slice' },
  { id: 'icons', title: 'Icon / ảnh UI lẻ', text: 'Làm nhanh một bộ icon hoặc vài ảnh giao diện riêng lẻ.', recipes: ['icon-pack', 'ui-kit', 'ui-screen'] },
  { id: 'prop3d', title: 'Vật phẩm 3D', text: 'Tạo một món đồ 3D như kiếm, thùng, cây rồi xuất ra ảnh hoặc mô hình.', recipes: ['prop-3d', 'icon-3d'] },
  { id: 'gdd', title: 'Từ tài liệu GDD', text: 'Dán GDD, Claude tách danh sách asset và chọn cách làm cho từng mục.', gdd: true },
  { id: 'pixel', title: 'Làm sạch pixel-art', text: 'Đưa ảnh pixel bị nhòe hoặc lẫn màu về điểm ảnh sắc nét, đúng bảng màu.', recipes: ['pixel-cleanup'] }
]

const emit = defineEmits<{ recipe: [id: string]; flow: [id: string]; gdd: []; count: [n: number] }>()

const recipes = useRecipes()
const flows = useFlows()
const fit = useFit()
const { advanced } = useUiMode()
const demos = ref<Record<string, RecipeDemo | null>>({})

onMounted(() => {
  void recipes.load()
  if (!flows.defsLoaded) void flows.loadDefs()
  void fit.load()
})

interface Card {
  goal: Goal
  flowDef?: WorkflowDef
  options: RecipeInfo[]
  previewFrom: RecipeInfo[]
}

const cards = computed<Card[]>(() =>
  GOALS.flatMap((goal) => {
    if (goal.gdd) return [{ goal, options: [], previewFrom: [] }]
    if (goal.flow) {
      const flowDef = flows.byId(goal.flow)
      if (!flowDef) return []
      const previewFrom = flowDef.nodes.map((n) => recipes.byId(n.recipe_id)).filter((r): r is RecipeInfo => !!r)
      return [{ goal, flowDef, options: [], previewFrom }]
    }
    const options = (goal.recipes ?? []).map((id) => recipes.byId(id)).filter((r): r is RecipeInfo => !!r)
    return options.length ? [{ goal, options, previewFrom: options }] : []
  })
)

const fitOf = (c: Card) => (c.flowDef ? fit.flow(c.flowDef.id) : c.options[0] ? fit.recipe(c.options[0].id) : null)

// Simple mode moves unfit goals to the end (dimmed, still clickable) so the first row is what the machine can do.
const shown = computed(() => {
  if (advanced.value) return cards.value
  const red = (c: Card): number => (fitOf(c)?.level === 'red' ? 1 : 0)
  return [...cards.value].sort((a, b) => red(a) - red(b))
})

watch(
  cards,
  (list) => {
    emit('count', list.length)
    for (const c of list) void loadDemo(c)
  },
  { immediate: true }
)

async function loadDemo(c: Card): Promise<void> {
  const id = c.goal.id
  if (id in demos.value) return
  demos.value[id] = null
  for (const r of c.previewFrom) {
    const d = await recipeDemo(r).catch(() => null)
    if (d) {
      demos.value[id] = d
      return
    }
  }
}

function open(c: Card): void {
  if (c.goal.gdd) emit('gdd')
  else if (c.flowDef) emit('flow', c.flowDef.id)
  else if (c.options[0]) emit('recipe', c.options[0].id)
}
</script>

<template>
  <section v-if="cards.length" class="goals" aria-labelledby="goals-h">
    <h2 id="goals-h" class="section-label">Tôi cần…</h2>
    <div class="g-grid">
      <div v-for="c in shown" :key="c.goal.id" class="g-card card" :class="{ unfit: !advanced && fitOf(c)?.level === 'red' }">
        <button type="button" class="g-main" @click="open(c)">
          <span class="g-art">
            <RecipeDemoImage v-if="demos[c.goal.id]" :demo="demos[c.goal.id]!" :alt="`Ảnh mẫu: ${c.goal.title}`" @failed="demos[c.goal.id] = null" />
            <FileText v-else-if="c.goal.gdd" :size="28" :stroke-width="1.5" aria-hidden="true" />
            <Boxes v-else :size="28" :stroke-width="1.5" aria-hidden="true" />
          </span>
          <FitBadge :entry="fitOf(c)" class="g-fit" />
          <span class="headline">{{ c.goal.title }}</span>
          <span class="callout g-text">{{ c.goal.text }}</span>
        </button>
        <span v-if="c.options.length > 1" class="g-alts">
          <span class="caption">Hoặc</span>
          <button v-for="r in c.options.slice(1)" :key="r.id" type="button" class="g-alt" :title="r.description" @click="emit('recipe', r.id)">
            {{ r.name }}
          </button>
        </span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.goals {
  display: grid;
  gap: var(--sp-3);
}
.g-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: var(--sp-4);
}
.g-card {
  display: flex;
  flex-direction: column;
  padding: 0;
  position: relative;
  overflow: hidden;
  transition: background-color var(--t-fast) var(--ease-out);
}
.g-card.unfit .g-main {
  opacity: 0.55;
}
.g-fit {
  position: absolute;
  top: 8px;
  right: 8px;
  
}
.g-card:hover {
  background: var(--surface-2);
}
.g-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 0 0 var(--sp-4);
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.g-main > .headline,
.g-main > .g-text {
  padding: 0 var(--sp-4);
}
.g-art {
  display: grid;
  place-items: center;
  width: 100%;
  aspect-ratio: 2 / 1;
  margin-bottom: var(--sp-2);
  overflow: hidden;
  color: var(--accent);
  background: var(--surface-2);
}
.g-text {
  color: var(--text-2);
}
.g-alts {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 0 var(--sp-4) var(--sp-4);
  color: var(--text-3);
}
.g-alt {
  padding: 1px 8px;
  border: 0;
  border-radius: 999px;
  background: var(--bg-active);
  color: var(--text-2);
  font: inherit;
  font-size: var(--fs-caption);
  cursor: pointer;
}
.g-alt:hover {
  color: var(--text);
}
</style>

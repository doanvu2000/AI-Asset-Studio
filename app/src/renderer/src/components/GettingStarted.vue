<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Check, ChevronRight, LoaderCircle } from '@lucide/vue'
import type { ToolPath } from '@/api/types'
import { ART } from '@/assets/art'
import { api, useConnection } from '@/stores/connection'
import { useRecipes } from '@/stores/recipes'
import { useRuns } from '@/stores/runs'

const conn = useConnection()
const recipes = useRecipes()
const runs = useRuns()

const tools = ref<ToolPath[] | null>(null)
const toolsError = ref(false)
watch(
  () => conn.transport,
  async (t) => {
    if (!t) return
    try {
      tools.value = (await api().toolPaths(false)).tools
      toolsError.value = false
    } catch {
      toolsError.value = true
    }
  },
  { immediate: true }
)

function readLast(): string | null {
  try {
    return localStorage.getItem('studio.newrun.recipe')
  } catch {
    return null
  }
}
const lastRecipe = readLast()

const toolsOk = computed(() => !!tools.value?.length && tools.value.every((t) => t.ok))
const toolsFound = computed(() => tools.value?.filter((t) => t.ok).length ?? 0)
const recipeChosen = computed(() => !!lastRecipe && !!recipes.byId(lastRecipe))
const firstAsset = computed(() => runs.runList.some((r) => r.status === 'succeeded'))
const startRecipe = computed(() => (recipeChosen.value ? lastRecipe! : (recipes.byId('concept-2d')?.id ?? recipes.list[0]?.id)))

const steps = computed(() => [
  {
    id: 'tools',
    title: 'Kiểm tra công cụ',
    done: toolsOk.value,
    busy: !tools.value && !toolsError.value,
    text: toolsError.value
      ? 'Chưa đọc được đường dẫn công cụ.'
      : tools.value
        ? toolsOk.value
          ? 'ComfyUI, Blender và Codex đều đã tìm thấy.'
          : `Đã tìm thấy ${toolsFound.value}/${tools.value.length} công cụ. Bấm Cài tự động để tải phần còn thiếu, hoặc tự đặt đường dẫn trong Cài đặt.`
        : 'Đang kiểm tra…',
    to: { path: '/settings', query: { pane: toolsOk.value ? 'tools' : 'setup' } },
    action: toolsOk.value ? 'Xem' : 'Cài tự động'
  },
  {
    id: 'recipe',
    title: 'Chọn quy trình',
    done: recipeChosen.value,
    busy: false,
    text: recipeChosen.value
      ? `Đã chọn “${recipes.byId(lastRecipe!)?.name}”.`
      : 'Mỗi quy trình tạo ra một loại asset: ảnh 2D, mesh 3D, sprite 8 hướng…',
    to: { path: '/new' },
    action: 'Xem quy trình'
  },
  {
    id: 'asset',
    title: 'Tạo asset đầu tiên',
    done: firstAsset.value,
    busy: false,
    text: 'Nhập một câu mô tả, chọn ảnh ưng ý, rồi duyệt vào thư viện.',
    to: { path: '/new', query: startRecipe.value ? { recipe: startRecipe.value } : {} },
    action: 'Bắt đầu'
  }
])
const nextId = computed(() => steps.value.find((s) => !s.done)?.id)
</script>

<template>
  <section class="gs card" aria-labelledby="gs-title">
    <img class="art" :src="ART.gettingStarted" alt="" width="148" height="148" draggable="false" />
    <div class="body">
      <h2 id="gs-title" class="large-title">Bắt đầu</h2>
      <p class="secondary lead">Ba bước để có asset đầu tiên trong thư viện.</p>
      <ol class="steps">
        <li v-for="(s, i) in steps" :key="s.id" class="step" :class="{ done: s.done, next: s.id === nextId }">
          <span class="mark" :aria-label="s.done ? 'Đã xong' : 'Chưa xong'">
            <LoaderCircle v-if="s.busy" :size="14" class="spin" />
            <Check v-else-if="s.done" :size="14" :stroke-width="3" />
            <span v-else class="num">{{ i + 1 }}</span>
          </span>
          <span class="s-text">
            <span class="headline">{{ s.title }}</span>
            <span class="callout">{{ s.text }}</span>
          </span>
          <RouterLink :to="s.to" class="btn" :class="{ 'btn-primary': s.id === nextId }">
            {{ s.action }}
            <ChevronRight v-if="s.id === nextId" />
          </RouterLink>
        </li>
      </ol>
    </div>
  </section>
</template>

<style scoped>
.gs {
  display: flex;
  align-items: center;
  gap: var(--sp-6);
  padding: var(--sp-6) var(--sp-8) var(--sp-6) var(--sp-6);
  background:
    radial-gradient(120% 140% at 0% 0%, var(--accent-soft), transparent 55%),
    var(--bg-elevated);
}
.art {
  flex: none;
  width: 148px;
  height: 148px;
  object-fit: contain;
  user-select: none;
}
.body {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 4px;
}
.lead {
  margin-bottom: var(--sp-3);
}
.steps {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
}
.step {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
}
.step + .step {
  border-top: 0.5px solid var(--separator);
}
.mark {
  flex: none;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  font-size: var(--fs-caption);
  font-weight: 700;
  color: var(--text-2);
  box-shadow: inset 0 0 0 1.5px var(--separator-strong);
  transition:
    background-color var(--t-base),
    color var(--t-base);
}
.next .mark {
  color: var(--accent);
  box-shadow: inset 0 0 0 1.5px var(--accent);
}
.done .mark {
  color: var(--bg-content);
  background: var(--st-ok);
  box-shadow: none;
}
.s-text {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 1px;
}
.done .headline {
  color: var(--text-2);
}
.spin {
  animation: spin 1s linear infinite;
}
@media (max-width: 720px) {
  .art {
    display: none;
  }
}
</style>

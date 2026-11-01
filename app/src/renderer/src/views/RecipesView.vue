<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { BookOpen, Copy, Eye, Pencil, Plus, RotateCw } from '@lucide/vue'
import type { RecipeInfo } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import RecipeThumb from '@/components/RecipeThumb.vue'
import LaneTag from '@/components/LaneTag.vue'
import RecipePeek from '@/components/RecipePeek.vue'
import RecipeQuickView from '@/components/RecipeQuickView.vue'
import { stepName } from '@/composables/format'
import { useRecipes } from '@/stores/recipes'
import { useConnection } from '@/stores/connection'

const recipes = useRecipes()
const conn = useConnection()
const router = useRouter()

watch(
  () => conn.transport,
  (t) => t && void recipes.load(true),
  { immediate: true }
)

// Built-in is the default, so it gets no badge; only recipes the user owns or changed are marked.
const SOURCE: Record<string, { label: string; tone: string } | undefined> = {
  user: { label: 'Của tôi', tone: 'user' },
  override: { label: 'Đã sửa', tone: 'edit' }
}
const sorted = computed(() => [...recipes.list].sort((a, b) => a.name.localeCompare(b.name, 'vi')))
const FLOW_SHOWN = 5
const flowNames = (r: RecipeInfo): string[] => [...new Set(r.steps.map((s) => stepName(s)))]
const flow = (r: RecipeInfo): string => {
  const names = flowNames(r)
  const rest = names.length - FLOW_SHOWN
  return rest > 1 ? `${names.slice(0, FLOW_SHOWN).join(' · ')} · +${rest} bước nữa` : names.join(' · ')
}
const PEEK_DELAY_MS = 300
const peek = ref<{ recipe: RecipeInfo; anchor: DOMRect } | null>(null)
const quick = ref<RecipeInfo | null>(null)
let peekTimer = 0
// Moving between cards while a popover is open swaps it at once; the delay only guards the first open.
function peekOn(r: RecipeInfo, e: Event): void {
  if (quick.value) return
  const anchor = (e.currentTarget as HTMLElement).getBoundingClientRect()
  window.clearTimeout(peekTimer)
  if (peek.value) peek.value = { recipe: r, anchor }
  else peekTimer = window.setTimeout(() => (peek.value = { recipe: r, anchor }), PEEK_DELAY_MS)
}
function peekOff(): void {
  window.clearTimeout(peekTimer)
  peek.value = null
}
function openQuick(r: RecipeInfo): void {
  peekOff()
  quick.value = r
}
onBeforeUnmount(() => window.clearTimeout(peekTimer))
const lanes = (r: RecipeInfo) => [...new Set(r.steps.map((s) => s.lane).filter((l) => l && l !== 'none'))]
</script>

<template>
  <div class="view">
    <PageHeader title="Quy trình" subtitle="Các bước để tạo ra từng loại asset">
      <button class="btn btn-primary" @click="router.push('/recipes/new')"><Plus /> Quy trình mới</button>
    </PageHeader>
    <div class="scroll">
      <div class="stack stack-wide">
        <EmptyState
          v-if="(recipes.loaded || recipes.error) && !recipes.list.length"
          :icon="BookOpen"
          :title="recipes.error ? 'Không tải được quy trình' : 'Chưa có quy trình nào'"
          :text="recipes.error ?? 'Tạo một quy trình mới, hoặc kiểm tra kết nối tới core.'"
          action-label="Quy trình mới"
          action-to="/recipes/new"
          :action-icon="Plus"
        >
          <button v-if="recipes.error" class="btn" @click="recipes.load(true)"><RotateCw /> Thử lại</button>
        </EmptyState>
        <div v-else v-auto-animate class="grid">
          <article v-for="r in sorted" :key="r.id" class="rc card" @mouseenter="peekOn(r, $event)" @mouseleave="peekOff" @focusin="peekOn(r, $event)" @focusout="peekOff" @keydown.esc="peekOff">
            <RouterLink :to="`/recipes/${encodeURIComponent(r.id)}`" class="rc-main">
              <RecipeThumb :id="r.id" :size="52" />
              <div class="rc-text">
                <div class="rc-top">
                  <h2 class="headline name" :title="r.name">{{ r.name }}</h2>
                  <span v-if="SOURCE[r.source ?? 'builtin']" class="badge" :class="SOURCE[r.source ?? 'builtin']!.tone">{{ SOURCE[r.source ?? 'builtin']!.label }}</span>
                </div>
                <p class="caption desc" :title="flowNames(r).join(' → ')">{{ flow(r) }}</p>
                <div class="meta">
                  <span class="caption num">{{ r.steps.length }} bước</span>
                  <LaneTag v-for="l in lanes(r)" :key="l" :lane="l" />
                </div>
              </div>
            </RouterLink>
            <div class="rc-actions">
              <button type="button" class="btn btn-plain" :aria-label="`Xem nhanh ${r.name}`" title="Xem nhanh: sơ đồ và demo" @click="openQuick(r)"><Eye /> Xem nhanh</button>
              <RouterLink :to="`/recipes/${encodeURIComponent(r.id)}`" class="btn btn-plain"><Pencil /> Sửa</RouterLink>
              <RouterLink :to="{ path: '/recipes/new', query: { from: r.id } }" class="btn btn-plain"><Copy /> Nhân bản</RouterLink>
            </div>
          </article>
        </div>
      </div>
    </div>
    <Teleport to="body">
      <Transition name="peek">
        <RecipePeek v-if="peek" :key="peek.recipe.id" :recipe="peek.recipe" :anchor="peek.anchor" />
      </Transition>
    </Teleport>
    <RecipeQuickView
      :recipe="quick"
      @close="quick = null"
      @edit="(id) => router.push(`/recipes/${encodeURIComponent(id)}`)"
      @run="(id) => router.push({ path: '/new', query: { recipe: id } })"
    />
  </div>
</template>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 14px;
}
.rc {
  display: grid;
  overflow: hidden;
  transition:
    transform var(--t-fast) var(--ease-spring),
    box-shadow var(--t-base) var(--ease-out);
}
.rc:hover {
  box-shadow: var(--shadow-2), var(--hairline);
}
.rc-main {
  display: flex;
  gap: 14px;
  padding: 16px 16px 10px;
  color: inherit;
  text-decoration: none;
}
.rc-main:active {
  transform: scale(0.99);
}
.rc-text {
  display: grid;
  gap: 4px;
  min-width: 0;
  flex: 1;
}
.rc-top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.name {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}
.desc {
  min-height: 4.3em;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
}
.badge {
  flex: none;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  font-weight: 650;
  color: var(--text-2);
  background: var(--bg-active);
}
.badge.user {
  color: var(--accent);
  background: var(--accent-soft);
}
.badge.edit {
  color: var(--st-waiting);
  background: var(--st-waiting-soft);
}
.rc-actions {
  display: flex;
  gap: 4px;
  padding: 6px 10px 10px;
  border-top: 0.5px solid var(--separator);
}
.rc-actions .btn {
  height: 28px;
  font-size: var(--fs-callout);
}
.rc-actions {
  flex-wrap: wrap;
}
.peek-enter-active,
.peek-leave-active {
  transition:
    opacity 150ms var(--ease-out),
    transform 150ms var(--ease-out);
}
.peek-enter-from,
.peek-leave-to {
  opacity: 0;
  transform: scale(0.97);
}
@media (prefers-reduced-motion: reduce) {
  .peek-enter-active,
  .peek-leave-active {
    transition: none;
  }
}
</style>

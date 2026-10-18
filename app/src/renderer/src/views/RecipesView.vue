<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { BookOpen, Copy, Pencil, Plus } from '@lucide/vue'
import type { RecipeInfo } from '@/api/types'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import RecipeThumb from '@/components/RecipeThumb.vue'
import LaneTag from '@/components/LaneTag.vue'
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

const SOURCE: Record<string, { label: string; tone: string }> = {
  builtin: { label: 'Gốc', tone: 'muted' },
  user: { label: 'Của bạn', tone: 'user' },
  override: { label: 'Đã sửa', tone: 'edit' }
}
const sorted = computed(() => [...recipes.list].sort((a, b) => a.name.localeCompare(b.name, 'vi')))
const lanes = (r: RecipeInfo) => [...new Set(r.steps.map((s) => s.lane).filter((l) => l && l !== 'none'))]
</script>

<template>
  <div class="view">
    <PageHeader title="Recipe" subtitle="Xem và chỉnh chuỗi bước của từng recipe">
      <button class="btn btn-primary" @click="router.push('/recipes/new')"><Plus /> Recipe mới</button>
    </PageHeader>
    <div class="scroll">
      <div class="stack">
        <EmptyState v-if="recipes.loaded && !recipes.list.length" :icon="BookOpen" title="Chưa có recipe" :text="recipes.error ?? 'Tạo recipe mới để bắt đầu.'" />
        <div v-else v-auto-animate class="grid">
          <article v-for="r in sorted" :key="r.id" class="rc card">
            <RouterLink :to="`/recipes/${encodeURIComponent(r.id)}`" class="rc-main">
              <RecipeThumb :id="r.id" :size="52" />
              <div class="rc-text">
                <div class="rc-top">
                  <h2 class="headline name">{{ r.name }}</h2>
                  <span class="badge" :class="SOURCE[r.source ?? 'builtin']?.tone">{{ SOURCE[r.source ?? 'builtin']?.label }}</span>
                </div>
                <p class="caption desc">{{ r.description }}</p>
                <div class="meta">
                  <span class="caption num">{{ r.steps.length }} bước</span>
                  <LaneTag v-for="l in lanes(r)" :key="l" :lane="l" />
                </div>
              </div>
            </RouterLink>
            <div class="rc-actions">
              <RouterLink :to="`/recipes/${encodeURIComponent(r.id)}`" class="btn btn-plain"><Pencil /> Sửa</RouterLink>
              <RouterLink :to="{ path: '/recipes/new', query: { from: r.id } }" class="btn btn-plain"><Copy /> Nhân bản</RouterLink>
            </div>
          </article>
        </div>
      </div>
    </div>
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
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.desc {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  min-height: 2.9em;
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
  font-size: 10.5px;
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
</style>

<script setup lang="ts">
import { watch } from 'vue'
import { ChevronRight, Layers, Plus } from '@lucide/vue'
import PageHeader from '@/components/PageHeader.vue'
import EmptyState from '@/components/EmptyState.vue'
import RecipeThumb from '@/components/RecipeThumb.vue'
import RelTime from '@/components/RelTime.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import { useRecipes } from '@/stores/recipes'
import { useBatches } from '@/stores/batches'
import { useConnection } from '@/stores/connection'

const recipes = useRecipes()
const batches = useBatches()
const conn = useConnection()

watch(
  () => conn.transport,
  (t) => t && void recipes.load(),
  { immediate: true }
)
</script>

<template>
  <div class="view">
    <PageHeader title="Hàng loạt" subtitle="Một quy trình, nhiều prompt hoặc nhiều file">
      <RouterLink :to="{ path: '/new', query: { mode: 'batch' } }" class="btn btn-primary"><Plus /> Tạo lô mới</RouterLink>
    </PageHeader>
    <div class="scroll">
      <div class="stack">
        <EmptyState
          v-if="!batches.list.length"
          :icon="Layers"
          title="Chưa có lô nào"
          text="Dán một danh sách prompt, mỗi dòng một asset, để chạy nhiều asset trong một lần."
          action-label="Tạo lô mới"
          :action-to="{ path: '/new', query: { mode: 'batch' } }"
          :action-icon="Plus"
        />
        <div v-else v-auto-animate class="batches">
          <RouterLink v-for="b in batches.list" :key="b.id" :to="`/batch/${b.id}`" class="batch card">
            <RecipeThumb :id="b.recipeId" :size="40" />
            <div class="b-text">
              <span class="b-title">{{ b.title || recipes.byId(b.recipeId)?.name || 'Lô không tên' }}</span>
              <span class="caption"><RelTime :ts="b.createdAt" /> · <span class="num">{{ b.runs.length }}</span> lượt</span>
              <div class="bar" role="img" :aria-label="`${b.done}/${b.runs.length} xong`">
                <span class="seg-ok" :style="{ transform: `scaleX(${b.done / b.runs.length})` }" />
                <span class="seg-bad" :style="{ transform: `scaleX(${b.failed / b.runs.length})`, left: `${(b.done / b.runs.length) * 100}%` }" />
              </div>
            </div>
            <div class="b-stats num">
              <span class="ok"><TweenNumber :value="b.done" />/{{ b.runs.length }}</span>
              <span v-if="b.awaiting" class="human">{{ b.awaiting }} chờ duyệt</span>
              <span v-else-if="b.running" class="run">{{ b.running }} đang chạy</span>
              <span v-if="b.failed" class="bad">{{ b.failed }} lỗi</span>
            </div>
            <ChevronRight :size="16" class="chev" />
          </RouterLink>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stack {
  max-width: 880px;
}
.batches {
  display: grid;
  gap: 8px;
}
.batch {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 14px;
  color: inherit;
  text-decoration: none;
  transition:
    transform var(--t-fast) var(--ease-spring),
    box-shadow var(--t-base) var(--ease-out);
}
.batch:hover {
  box-shadow: var(--shadow-2), var(--hairline);
}
.batch:active {
  transform: scale(0.995);
}
.b-text {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 3px;
}
.b-title {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.bar {
  position: relative;
  height: 5px;
  margin-top: 4px;
  border-radius: 3px;
  overflow: hidden;
  background: var(--bg-active);
}
.bar span {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 100%;
  transform-origin: left;
  transition: transform 420ms var(--ease-out);
}
.seg-ok {
  background: var(--st-ok);
}
.seg-bad {
  background: var(--st-fail);
}
.b-stats {
  display: grid;
  justify-items: end;
  gap: 2px;
  font-size: var(--fs-callout);
}
.ok {
  font-weight: 650;
}
.run {
  color: var(--st-running);
}
.human {
  color: var(--st-human);
  font-weight: 600;
}
.bad {
  color: var(--st-fail);
}
.chev {
  color: var(--text-3);
}
</style>

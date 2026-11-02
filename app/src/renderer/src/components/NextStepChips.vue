<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight } from '@lucide/vue'
import type { BlobRef, Outputs } from '@/api/types'
import { useRecipes } from '@/stores/recipes'
import { followUps, prefillQuery } from '@/composables/followUp'
import { featuredAsset, outputBlobs } from '@/components/viewers/detect'
import RecipeThumb from './RecipeThumb.vue'

const props = defineProps<{ recipeId: string; outputs: Outputs; projectId?: string }>()

const MAX = 3
const recipes = useRecipes()
const router = useRouter()
onMounted(() => void recipes.load())

const source = computed<BlobRef | undefined>(() => {
  const blobs = outputBlobs(props.outputs)
  return featuredAsset(blobs)?.blob ?? blobs[0]
})

// Recipes the author declared as the natural next step (`then`) come first, the rest by how complete the prefill is.
const chips = computed(() => {
  const blob = source.value
  if (!blob) return []
  const declared = new Set((recipes.byId(props.recipeId)?.then ?? []).map((t) => t.recipe_id))
  return followUps(recipes.list, blob, props.outputs, props.recipeId)
    .filter((f) => !f.missing.length && f.recipe.id !== props.recipeId)
    .sort((a, b) => Number(declared.has(b.recipe.id)) - Number(declared.has(a.recipe.id)))
    .slice(0, MAX)
})

function go(i: number): void {
  const f = chips.value[i]
  if (f) void router.push({ path: '/new', query: prefillQuery(f.recipe.id, f.prefill, props.projectId) })
}
</script>

<template>
  <nav v-if="chips.length" class="next" aria-label="Làm tiếp">
    <span class="caption next-label">Làm tiếp</span>
    <button v-for="(f, i) in chips" :key="f.recipe.id" type="button" class="next-chip" :title="f.recipe.description" @click="go(i)">
      <RecipeThumb :id="f.recipe.id" :size="22" />
      {{ f.recipe.name }}
      <ArrowRight :size="13" aria-hidden="true" />
    </button>
  </nav>
</template>

<style scoped>
.next {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  flex: none;
  padding: 8px var(--sp-6) 0;
}
.next-label {
  color: var(--text-3);
}
.next-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px 3px 4px;
  border: 0;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font: inherit;
  font-size: var(--fs-callout, 13px);
  font-weight: 500;
  cursor: pointer;
  transition:
    background var(--t-fast) var(--ease-out),
    scale var(--t-fast) var(--ease-out);
}
.next-chip:hover {
  background: var(--bg-active);
}
.next-chip:active {
  scale: 0.97;
}
@media (prefers-reduced-motion: reduce) {
  .next-chip {
    transition: none;
  }
}
</style>

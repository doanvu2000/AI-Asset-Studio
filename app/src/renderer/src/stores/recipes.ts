import { defineStore } from 'pinia'
import { ref, shallowRef } from 'vue'
import type { RecipeInfo } from '@/api/types'
import { api } from './connection'

export const useRecipes = defineStore('recipes', () => {
  const list = shallowRef<RecipeInfo[]>([])
  const loaded = ref(false)
  const error = ref<string | null>(null)

  async function load(force = false): Promise<void> {
    if (loaded.value && !force) return
    try {
      list.value = await api().recipes()
      error.value = null
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loaded.value = true
    }
  }

  function byId(id: string): RecipeInfo | undefined {
    return list.value.find((r) => r.id === id)
  }

  return { list, loaded, error, load, byId }
})

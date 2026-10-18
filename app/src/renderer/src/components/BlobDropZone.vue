<script setup lang="ts">
import { computed, ref } from 'vue'
import { FileBox, ImagePlus, LoaderCircle, X } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import { api } from '@/stores/connection'
import { bytes } from '@/composables/format'
import BlobImage from './BlobImage.vue'

const props = defineProps<{ kind: 'image' | 'images' | 'mesh'; label: string; invalid?: boolean }>()
const model = defineModel<BlobRef[]>({ default: () => [] })

const over = ref(false)
const uploading = ref(0)
const error = ref<string | null>(null)
const input = ref<HTMLInputElement | null>(null)

const multiple = computed(() => props.kind === 'images')
const accept = computed(() => (props.kind === 'mesh' ? '.glb,model/gltf-binary' : 'image/png,image/jpeg,image/webp'))

function accepts(f: File): boolean {
  if (props.kind === 'mesh') return f.name.toLowerCase().endsWith('.glb')
  return f.type.startsWith('image/')
}

async function take(files: FileList | File[] | null): Promise<void> {
  error.value = null
  const list = [...(files ?? [])].filter(accepts)
  if (!list.length) {
    error.value = props.kind === 'mesh' ? 'Chỉ nhận file .glb' : 'Chỉ nhận ảnh PNG, JPEG hoặc WebP'
    return
  }
  const chosen = multiple.value ? list : list.slice(0, 1)
  uploading.value += chosen.length
  try {
    const refs = await Promise.all(chosen.map((f) => api().uploadBlob(f).finally(() => (uploading.value -= 1))))
    model.value = multiple.value ? [...model.value, ...refs] : refs
  } catch (e) {
    error.value = `Tải lên thất bại: ${(e as Error).message}`
  }
}

function onDrop(e: DragEvent): void {
  over.value = false
  void take(e.dataTransfer?.files ?? null)
}

function remove(hash: string): void {
  model.value = model.value.filter((b) => b.hash !== hash)
}
</script>

<template>
  <div class="dz-wrap">
    <div
      class="dz"
      :class="{ over, invalid, filled: model.length > 0 && !multiple }"
      role="button"
      tabindex="0"
      :aria-label="`${label}: kéo thả hoặc bấm để chọn file`"
      @click="input?.click()"
      @keydown.enter.prevent="input?.click()"
      @keydown.space.prevent="input?.click()"
      @dragenter.prevent="over = true"
      @dragover.prevent="over = true"
      @dragleave.prevent="over = false"
      @drop.prevent="onDrop"
    >
      <template v-if="uploading">
        <LoaderCircle class="spin" :size="20" />
        <span class="secondary">Đang tải lên…</span>
      </template>
      <template v-else-if="model.length && !multiple">
        <div v-if="kind === 'mesh'" class="mesh">
          <FileBox :size="22" />
          <span>
            <strong>{{ model[0]!.name ?? 'mesh.glb' }}</strong>
            <span class="caption">{{ bytes(model[0]!.size) }}</span>
          </span>
        </div>
        <BlobImage v-else :hash="model[0]!.hash" :size="256" full class="single" />
        <span class="caption">Bấm hoặc thả file khác để thay</span>
      </template>
      <template v-else>
        <component :is="kind === 'mesh' ? FileBox : ImagePlus" :size="22" class="dz-ic" />
        <span><strong>Thả {{ kind === 'mesh' ? 'file .glb' : 'ảnh' }} vào đây</strong></span>
        <span class="caption">hoặc bấm để chọn{{ multiple ? ' (nhiều file)' : '' }}</span>
      </template>
      <input
        ref="input"
        type="file"
        class="sr-only"
        :accept="accept"
        :multiple="multiple"
        tabindex="-1"
        @change="take(($event.target as HTMLInputElement).files)"
      />
    </div>
    <div v-if="multiple && model.length" v-auto-animate class="thumbs">
      <div v-for="b in model" :key="b.hash" class="thumb">
        <BlobImage :hash="b.hash" :size="128" />
        <button class="rm" :aria-label="`Bỏ ${b.name ?? 'ảnh'}`" @click="remove(b.hash)"><X :size="12" /></button>
      </div>
    </div>
    <p v-if="error" class="err" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.dz {
  display: grid;
  justify-items: center;
  align-content: center;
  gap: 6px;
  min-height: 132px;
  padding: 18px;
  border-radius: var(--r-md);
  border: 1.5px dashed var(--separator-strong);
  background: var(--bg-sunken);
  text-align: center;
  cursor: pointer;
  transition:
    border-color var(--t-fast),
    background-color var(--t-fast),
    transform var(--t-fast) var(--ease-spring);
}
.dz:hover {
  border-color: var(--accent);
}
.dz.over {
  border-color: var(--accent);
  background: var(--accent-soft);
  transform: scale(1.01);
}
.dz.invalid {
  border-color: var(--st-fail);
}
.dz-ic {
  color: var(--accent);
}
.single {
  width: 120px;
  border-radius: var(--r-sm);
  box-shadow: var(--hairline);
}
.mesh {
  display: flex;
  align-items: center;
  gap: 10px;
  text-align: left;
}
.mesh span {
  display: grid;
}
.thumbs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}
.thumb {
  position: relative;
  width: 64px;
  border-radius: var(--r-sm);
  overflow: hidden;
  box-shadow: var(--hairline);
}
.rm {
  position: absolute;
  top: 3px;
  right: 3px;
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border: 0;
  border-radius: 50%;
  color: #fff;
  background: rgba(0, 0, 0, 0.55);
  cursor: pointer;
}
.err {
  margin-top: 6px;
  font-size: var(--fs-callout);
  color: var(--st-fail);
}
.spin {
  animation: spin 1s linear infinite;
  color: var(--accent);
}
</style>

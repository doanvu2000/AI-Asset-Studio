<script setup lang="ts">
import { computed, ref } from 'vue'
import { FileBox, FileText, Film, ImagePlus, LoaderCircle, X } from '@lucide/vue'
import type { BlobRef } from '@/api/types'
import { api, blobUrl } from '@/stores/connection'
import { bytes } from '@/composables/format'
import BlobImage from './BlobImage.vue'

type Kind = 'image' | 'images' | 'mesh' | 'file' | 'video'
const props = defineProps<{ kind: Kind; label: string; invalid?: boolean; ext?: string[]; siblings?: string }>()
const model = defineModel<BlobRef[]>({ default: () => [] })

const over = ref(false)
const uploading = ref(0)
const error = ref<string | null>(null)
const input = ref<HTMLInputElement | null>(null)

const VIDEO_EXT = ['mp4', 'webm', 'gif', 'mov', 'mkv']
const atlasPages = computed(() => props.kind === 'file' && props.siblings === 'atlas_pages')
const aseSheet = computed(() => props.kind === 'file' && props.siblings === 'aseprite_sheet')
const withPng = computed(() => atlasPages.value || aseSheet.value)
const multiple = computed(() => props.kind === 'images' || withPng.value)
const extOf = (name: string): string => name.toLowerCase().split('.').pop() ?? ''
const fileExts = computed(() => (props.kind === 'video' ? VIDEO_EXT : (props.ext ?? [])))
const accept = computed(() => {
  if (props.kind === 'mesh') return '.glb,model/gltf-binary'
  if (props.kind === 'file' || props.kind === 'video') {
    const list = fileExts.value.map((e) => `.${e}`)
    return (withPng.value ? [...list, '.png'] : list).join(',')
  }
  return 'image/png,image/jpeg,image/webp'
})
const kindLabel = computed(() => {
  if (props.kind === 'mesh') return 'file .glb'
  if (props.kind === 'video') return 'video'
  if (props.kind === 'file') return fileExts.value.length ? `file .${fileExts.value.join(' / .')}` : 'file'
  return 'ảnh'
})

function accepts(f: File): boolean {
  if (props.kind === 'mesh') return f.name.toLowerCase().endsWith('.glb')
  if (props.kind === 'video') return f.type.startsWith('video/') || VIDEO_EXT.includes(extOf(f.name))
  if (props.kind === 'file') return !fileExts.value.length || fileExts.value.includes(extOf(f.name)) || (withPng.value && extOf(f.name) === 'png')
  return f.type.startsWith('image/')
}

/** Page names of a Spine/libgdx atlas: the first line of the file and of every block after a blank line. */
function pageNames(text: string): string[] {
  const pages: string[] = []
  let fresh = true
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) fresh = true
    else if (fresh) {
      pages.push(line)
      fresh = false
    }
  }
  return pages
}

/** Atlas first, then its page images in atlas order; null + error when the atlas or a page is missing. */
async function atlasBundle(list: File[]): Promise<File[] | null> {
  const atlas = list.find((f) => extOf(f.name) !== 'png')
  if (!atlas) {
    error.value = `Cần file atlas (.${fileExts.value.join(' / .')}) kèm các trang png`
    return null
  }
  const byName = new Map(list.map((f) => [f.name.toLowerCase(), f]))
  const pages = pageNames(await atlas.text())
  const missing = pages.filter((p) => !byName.has(p.toLowerCase()))
  if (missing.length) {
    error.value = `Thiếu ${missing.length} trang ảnh của atlas: ${missing.slice(0, 3).join(', ')}${missing.length > 3 ? '…' : ''}. Chọn cùng lúc atlas và mọi trang png.`
    return null
  }
  return [atlas, ...pages.map((p) => byName.get(p.toLowerCase())!)]
}

/** A .aseprite file alone, or an Aseprite JSON sheet first and then the PNG its meta.image names. */
async function aseBundle(list: File[]): Promise<File[] | null> {
  const ase = list.find((f) => ['aseprite', 'ase'].includes(extOf(f.name)))
  if (ase) return [ase]
  const json = list.find((f) => extOf(f.name) === 'json')
  if (!json) {
    error.value = 'Cần file .aseprite, hoặc file JSON của Aseprite kèm PNG sheet'
    return null
  }
  let image = ''
  try {
    const doc = JSON.parse(await json.text()) as { meta?: { image?: unknown } }
    image = typeof doc.meta?.image === 'string' ? (doc.meta.image.split(/[\\/]/).pop() ?? '') : ''
  } catch {
    error.value = `${json.name} không phải JSON hợp lệ`
    return null
  }
  const pngs = list.filter((f) => extOf(f.name) === 'png')
  const png = pngs.find((f) => f.name.toLowerCase() === image.toLowerCase()) ?? (pngs.length === 1 ? pngs[0] : undefined)
  if (!png) {
    error.value = `Thiếu ảnh sheet${image ? ` ${image}` : ''}. Chọn cùng lúc file JSON và PNG.`
    return null
  }
  return [json, png]
}

async function take(files: FileList | File[] | null): Promise<void> {
  error.value = null
  const list = [...(files ?? [])].filter(accepts)
  if (!list.length) {
    error.value = props.kind === 'image' || props.kind === 'images' ? 'Chỉ nhận ảnh PNG, JPEG hoặc WebP' : `Chỉ nhận ${kindLabel.value}`
    return
  }
  const chosen = atlasPages.value ? await atlasBundle(list) : aseSheet.value ? await aseBundle(list) : multiple.value ? list : list.slice(0, 1)
  if (!chosen) return
  uploading.value += chosen.length
  try {
    const refs = await Promise.all(chosen.map((f) => api().uploadBlob(f).finally(() => (uploading.value -= 1))))
    model.value = props.kind === 'images' ? [...model.value, ...refs] : refs
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

const totalSize = computed(() => model.value.reduce((n, b) => n + (b.size ?? 0), 0))
</script>

<template>
  <div class="dz-wrap">
    <div
      class="dz"
      :class="{ over, invalid, filled: model.length > 0 && kind !== 'images' }"
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
      <template v-else-if="model.length && kind !== 'images'">
        <video v-if="kind === 'video'" :src="blobUrl(model[0]!.hash)" class="video" controls muted playsinline @click.stop />
        <div v-if="kind === 'video' || kind === 'file'" class="mesh">
          <component :is="kind === 'video' ? Film : FileText" :size="22" />
          <span>
            <strong>{{ model[0]!.name ?? kindLabel }}</strong>
            <span class="caption">{{ atlasPages ? `+ ${model.length - 1} trang ảnh · ` : aseSheet && model.length > 1 ? `+ ${model[1]!.name ?? 'PNG'} · ` : '' }}{{ bytes(totalSize) }}</span>
          </span>
        </div>
        <div v-else-if="kind === 'mesh'" class="mesh">
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
        <component :is="kind === 'mesh' ? FileBox : kind === 'video' ? Film : kind === 'file' ? FileText : ImagePlus" :size="22" class="dz-ic" />
        <span><strong>Thả {{ kindLabel }} vào đây</strong></span>
        <span class="caption">{{ atlasPages ? 'chọn cùng lúc atlas và mọi trang png của nó' : aseSheet ? 'file .aseprite, hoặc chọn cùng lúc JSON và PNG sheet' : `hoặc bấm để chọn${multiple ? ' (nhiều file)' : ''}` }}</span>
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
    <div v-if="kind === 'images' && model.length" v-auto-animate class="thumbs">
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
.video {
  width: min(100%, 320px);
  max-height: 220px;
  border-radius: var(--r-sm);
  background: #000;
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

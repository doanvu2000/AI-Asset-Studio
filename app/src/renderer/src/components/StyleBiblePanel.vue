<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { BookMarked, LoaderCircle, RotateCw, Save, Sparkles, X } from '@lucide/vue'
import type { StyleBible } from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import BlobImage from '@/components/BlobImage.vue'
import RelTime from '@/components/RelTime.vue'
import { api } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'

const props = defineProps<{ projectId: string }>()
const toasts = useToasts()

const LIGHT_VI: Record<string, string> = {
  right: 'phải', 'top right': 'trên-phải', top: 'trên', 'top left': 'trên-trái',
  left: 'trái', 'bottom left': 'dưới-trái', bottom: 'dưới', 'bottom right': 'dưới-phải'
}

const bible = ref<StyleBible | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
const building = ref(false)
const saving = ref(false)
const vision = ref(true)
const suffix = ref('')
const negative = ref('')

function adopt(b: StyleBible | null): void {
  bible.value = b
  suffix.value = b?.suffix ?? ''
  negative.value = b?.negative ?? ''
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    adopt((await api().getStyleBible(props.projectId)).bible)
  } catch (e) {
    loadError.value = (e as Error).message
  } finally {
    loading.value = false
  }
}
watch(() => props.projectId, () => void load(), { immediate: true })

const dirty = computed(() => !!bible.value && (suffix.value.trim() !== bible.value.suffix || negative.value.trim() !== bible.value.negative))

async function build(): Promise<void> {
  if (dirty.value && !window.confirm('Dựng lại sẽ thay phần chữ bạn đang sửa. Tiếp tục?')) return
  building.value = true
  try {
    const b = (await api().buildStyleBible(props.projectId, { vision: vision.value })).bible
    adopt(b)
    if (b?.vision_error) toasts.push(`Claude chưa viết được, dùng mô tả tự đo: ${b.vision_error}`, 'error', 6000)
    else toasts.push(`Đã dựng style bible bản ${b?.version}`, 'success')
  } catch (e) {
    toasts.error(e, 'Không dựng được style bible')
  } finally {
    building.value = false
  }
}

async function edit(body: Parameters<ReturnType<typeof api>['editStyleBible']>[1], ok?: string): Promise<void> {
  saving.value = true
  try {
    const draft = { suffix: suffix.value, negative: negative.value }
    adopt((await api().editStyleBible(props.projectId, body)).bible)
    // Toggling or dropping a ref must not throw away text the user is still editing.
    if (body.suffix === undefined) {
      suffix.value = draft.suffix
      negative.value = draft.negative
    }
    if (ok) toasts.push(ok, 'success', 2200)
  } catch (e) {
    toasts.error(e, 'Không lưu được style bible')
  } finally {
    saving.value = false
  }
}

const saveText = (): Promise<void> => edit({ suffix: suffix.value, negative: negative.value }, 'Đã lưu')
const setActive = (on: boolean): Promise<void> => edit({ active: on })
const dropRef = (hash: string): Promise<void> => edit({ refs: (bible.value?.refs ?? []).filter((r) => r.hash !== hash).map((r) => r.hash) })

const render = computed(() => (bible.value?.pixel_art ? 'Pixel art' : 'Vẽ tay / mượt'))
const outline = computed(() => {
  const b = bible.value
  if (!b || b.outlined === null) return 'không rõ'
  return b.outlined ? `có${b.outline_weight ? ` (~${b.outline_weight} px/256)` : ''}` : 'không'
})
</script>

<template>
  <section class="sbp">
    <div class="bar">
      <label v-if="bible" class="opt switch" :title="bible.active ? 'Lượt chạy mới trong dự án sẽ được áp style' : 'Đang tắt: lượt chạy mới không bị thêm gì'">
        <input type="checkbox" :checked="bible.active" :disabled="saving" @change="setActive(($event.target as HTMLInputElement).checked)" />
        <span>{{ bible.active ? 'Đang áp cho lượt chạy mới' : 'Đang tắt' }}</span>
      </label>
      <span v-if="bible?.built_at" class="caption">Bản {{ bible.version }} · dựng <RelTime :ts="bible.built_at" /> từ {{ bible.source_count }} asset đã duyệt</span>
      <div class="actions">
        <label class="opt caption" title="Claude xem các asset đã duyệt và viết đoạn mô tả style (tốn phí API)"><input v-model="vision" type="checkbox" /> <Sparkles :size="12" /> Claude viết mô tả</label>
        <button type="button" class="btn btn-primary" :disabled="building" @click="build">
          <LoaderCircle v-if="building" class="spin" /> <RotateCw v-else-if="bible" /> <BookMarked v-else />
          {{ building ? (vision ? 'Đang đo và chờ Claude…' : 'Đang đo…') : bible ? 'Dựng lại' : 'Dựng style bible' }}
        </button>
      </div>
    </div>

    <p v-if="loading && !bible" class="caption state" role="status"><LoaderCircle class="spin" :size="14" /> Đang tải…</p>
    <EmptyState v-else-if="loadError" :icon="BookMarked" title="Không tải được style bible" :text="loadError">
      <button class="btn" @click="load">Thử lại</button>
    </EmptyState>
    <EmptyState
      v-else-if="!bible"
      :icon="BookMarked"
      title="Chưa có style bible"
      text="Đo bảng màu, nét viền, hướng sáng và chọn ảnh mẫu từ các asset đã duyệt, rồi tự thêm vào mọi lượt chạy mới của dự án để giữ cùng một phong cách."
    >
      <button class="btn btn-primary" :disabled="building" @click="build"><BookMarked /> Dựng style bible</button>
    </EmptyState>

    <template v-else>
      <div class="overview card" :data-off="!bible.active">
        <div class="ref">
          <span class="caption">Bảng màu</span>
          <span class="swatches"><span v-for="c in bible.palette" :key="c" class="sw" :style="{ background: c }" :title="c" /></span>
        </div>
        <div class="ref caption">
          <span>Kiểu vẽ: <b>{{ render }}</b></span>
          <span>Viền: <b>{{ outline }}</b></span>
          <span>Ánh sáng: <b>{{ bible.light_label ? LIGHT_VI[bible.light_label] ?? bible.light_label : 'không rõ' }}</b></span>
          <span v-if="bible.cost_usd" class="num">Claude ${{ bible.cost_usd.toFixed(3) }}</span>
        </div>
        <p v-if="bible.summary" class="vision"><Sparkles :size="13" /> {{ bible.summary }}</p>
      </div>

      <div class="card refs-card">
        <div class="head">
          <span class="f-label">Ảnh style mẫu</span>
          <span class="caption">Ảnh đầu tiên điền vào ô “ảnh style tham chiếu” của quy trình nếu bạn để trống</span>
        </div>
        <p v-if="!bible.refs.length" class="caption none">Không còn ảnh mẫu. Dựng lại để chọn ảnh mới.</p>
        <div v-auto-animate class="refs">
          <figure v-for="(r, i) in bible.refs" :key="r.hash" class="refimg">
            <BlobImage :hash="r.hash" :size="256" :alt="r.asset_name || `Ảnh mẫu ${i + 1}`" class="thumb" />
            <figcaption class="caption">{{ i === 0 ? 'Mặc định · ' : '' }}{{ r.asset_name || '—' }}</figcaption>
            <button type="button" class="btn btn-plain btn-icon rm" :disabled="saving" :aria-label="`Bỏ ảnh mẫu ${r.asset_name}`" title="Bỏ ảnh này" @click="dropRef(r.hash)"><X /></button>
          </figure>
        </div>
      </div>

      <form class="card text" novalidate @submit.prevent="saveText">
        <label class="f">
          <span class="f-label">Hậu tố prompt (tiếng Anh)</span>
          <textarea v-model="suffix" rows="3" maxlength="2000" class="field" placeholder="hand-painted, soft cel shading, clean dark outline…" />
          <span class="caption">Thêm vào cuối prompt (mỗi dòng nếu prompt nhiều dòng), hoặc vào ô Style nếu quy trình có ô đó và bạn để trống.</span>
        </label>
        <label class="f">
          <span class="f-label">Tránh (negative)</span>
          <textarea v-model="negative" rows="2" maxlength="2000" class="field" placeholder="photorealistic, 3d render, text…" />
        </label>
        <div class="foot">
          <span v-if="bible.edited" class="caption">Đã sửa tay</span>
          <button type="button" class="btn btn-plain" :disabled="!dirty || saving" @click="adopt(bible)">Hoàn tác</button>
          <button type="submit" class="btn btn-primary" :disabled="!dirty || saving"><Save /> Lưu</button>
        </div>
      </form>
    </template>
  </section>
</template>

<style scoped>
.sbp {
  display: grid;
  gap: 12px;
}
.bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-left: auto;
}
.opt {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
.switch {
  font-weight: 500;
}
.state {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.overview {
  display: grid;
  gap: 8px;
  padding: 14px 18px;
  transition: opacity var(--t-base);
}
.overview[data-off='true'] {
  opacity: 0.6;
}
.ref {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 14px;
}
.swatches {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
}
.sw {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  box-shadow: inset 0 0 0 0.5px rgb(0 0 0 / 0.25);
}
.vision {
  margin: 0;
  display: flex;
  gap: 6px;
  align-items: baseline;
  color: var(--text-2);
}
.refs-card {
  padding: 4px 0 12px;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 12px;
  padding: 10px 14px;
}
.none {
  margin: 0;
  padding: 0 14px;
}
.refs {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 0 14px;
}
.refimg {
  position: relative;
  margin: 0;
  display: grid;
  gap: 4px;
  width: 132px;
}
.thumb {
  width: 132px;
  height: 132px;
  border-radius: 10px;
  overflow: hidden;
}
.refimg figcaption {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rm {
  position: absolute;
  top: 4px;
  right: 4px;
  opacity: 0;
  background: var(--bg-elevated);
  transition: opacity var(--t-base);
}
.refimg:hover .rm,
.rm:focus-visible {
  opacity: 1;
}
.text {
  display: grid;
  gap: 12px;
  padding: 14px 18px;
}
.f {
  display: grid;
  gap: 5px;
}
.f-label {
  font-weight: 500;
  color: var(--text-2);
}
.f textarea {
  resize: vertical;
  font-family: inherit;
}
.foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
.foot .caption {
  margin-right: auto;
}
.spin {
  animation: spin 1s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .spin {
    animation: none;
  }
}
@media (hover: none) {
  .rm {
    opacity: 1;
  }
}
</style>

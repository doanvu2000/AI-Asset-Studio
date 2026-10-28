<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { onKeyStroke } from '@vueuse/core'
import { Download, LoaderCircle, TriangleAlert, X } from '@lucide/vue'
import BlobImage from '@/components/BlobImage.vue'
import ScoreBadge from '@/components/ScoreBadge.vue'
import { loadRounds, miniMarkdown, reviewImages, type ReviewRound, type StepReview } from '@/composables/review'

const props = defineProps<{ review: StepReview | null; open: boolean; oldSpec?: unknown }>()
const emit = defineEmits<{ close: [] }>()

const rounds = ref<ReviewRound[]>([])
const sel = ref(0)
const loading = ref(false)
const error = ref<string | null>(null)
const closeBtn = ref<HTMLButtonElement | null>(null)

watch(
  () => [props.open, props.review?.step.id] as const,
  async ([open]) => {
    if (!open || !props.review) return
    loading.value = true
    error.value = null
    try {
      rounds.value = await loadRounds(props.review, props.oldSpec)
      const best = rounds.value.reduce((b, r, i) => ((r.score ?? -1) > (rounds.value[b]?.score ?? -1) ? i : b), 0)
      sel.value = best
    } catch (e) {
      error.value = `Không đọc được kết quả chấm: ${(e as Error).message}`
      rounds.value = []
    } finally {
      loading.value = false
    }
    await nextTick()
    closeBtn.value?.focus()
  },
  { immediate: true }
)

const round = computed(() => rounds.value[sel.value] ?? null)
const images = computed(() => (props.review ? reviewImages(props.review) : { refs: [], inputs: [] }))
const critiqueHtml = computed(() => miniMarkdown(round.value?.critique ?? props.review?.critique ?? ''))
const warning = computed(() => {
  const w = props.review?.step.meta?.warning
  return typeof w === 'string' ? w : ''
})
const pct = (v: number): string => `${Math.round(v * 100)}%`
const fmt = (v: unknown): string => (v === null || v === undefined ? '—' : typeof v === 'string' ? v : JSON.stringify(v))

function download(): void {
  const spec = round.value?.nextSpec ?? round.value?.spec
  if (!spec) return
  const url = URL.createObjectURL(new Blob([JSON.stringify(spec, null, 2)], { type: 'application/json' }))
  const a = Object.assign(document.createElement('a'), { href: url, download: `motion_spec_round${round.value?.round ?? 1}.json` })
  a.click()
  URL.revokeObjectURL(url)
}

onKeyStroke('Escape', (e) => {
  if (!props.open) return
  e.preventDefault()
  emit('close')
})
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="open && review" class="scrim" role="dialog" aria-modal="true" aria-labelledby="rv-title" @click.self="emit('close')">
        <section class="panel">
          <header class="head">
            <h2 id="rv-title">Kết quả chấm</h2>
            <ScoreBadge :score="review.score" :history="review.history" size="md" />
            <span class="caption step">{{ review.step.key }}</span>
            <span class="spacer" />
            <button class="btn btn-plain small" :disabled="!round?.nextSpec && !round?.spec" @click="download">
              <Download :size="14" />
              Tải spec JSON
            </button>
            <button ref="closeBtn" class="btn btn-plain btn-icon" aria-label="Đóng (Esc)" title="Đóng (Esc)" @click="emit('close')"><X /></button>
          </header>

          <nav v-if="rounds.length > 1" class="rounds" role="tablist" aria-label="Các vòng chấm">
            <button
              v-for="(r, i) in rounds"
              :key="r.round"
              type="button"
              role="tab"
              :aria-selected="sel === i"
              :class="{ on: sel === i }"
              @click="sel = i"
            >
              Vòng {{ r.round }}
              <ScoreBadge v-if="r.score !== null" :score="r.score" />
              <span v-else class="caption">lỗi</span>
            </button>
          </nav>

          <p v-if="warning" class="warn" role="note"><TriangleAlert :size="14" /> {{ warning }}</p>

          <div v-if="loading" class="state"><LoaderCircle class="spin" :size="18" /> Đang tải…</div>
          <p v-else-if="error" class="err" role="alert">{{ error }}</p>
          <div v-else class="body">
            <div class="pics">
              <figure v-if="round?.strip">
                <figcaption class="caption">Bản nháp vòng {{ round.round }}</figcaption>
                <BlobImage :hash="round.strip" full class="img" />
              </figure>
              <figure v-if="round?.zoom">
                <figcaption class="caption">
                  Khớp gập sâu nhất<template v-if="round.bend"> · {{ round.bend.bone }} t={{ round.bend.time }}s · {{ round.bend.total }}° (giới hạn {{ round.bend.limit }}°)</template>
                </figcaption>
                <BlobImage :hash="round.zoom" full class="img zoom" />
              </figure>
              <figure v-if="round?.zoomBare">
                <figcaption class="caption" :class="{ bad: round.seamVisible }">
                  Cùng khớp, chỉ da{{ round.seamVisible ? ' · lộ mối nối' : '' }}
                </figcaption>
                <BlobImage :hash="round.zoomBare" full class="img zoom" />
              </figure>
              <figure v-if="round?.kneeZoom">
                <figcaption class="caption" :class="{ bad: round.knee?.overlap }">
                  Hai gối gần nhất<template v-if="round.knee"> · t={{ round.knee.time }}s · cách {{ round.knee.min_gap }}px = {{ pct(round.knee.ratio) }} so với setup{{ round.knee.overlap ? ' · đè nhau' : '' }}</template>
                </figcaption>
                <BlobImage :hash="round.kneeZoom" full class="img zoom" />
              </figure>
              <figure v-if="round?.handZoom">
                <figcaption class="caption">
                  Bàn tay lúc vung nhanh nhất<template v-if="round.hand"> · {{ round.hand.bone }} t={{ round.hand.time }}s</template>
                </figcaption>
                <BlobImage :hash="round.handZoom" full class="img zoom" />
              </figure>
              <figure v-for="v in round?.skirtViews ?? []" :key="v.image">
                <figcaption class="caption">Mặc {{ v.skin }}</figcaption>
                <BlobImage :hash="v.image" full class="img" />
              </figure>
              <figure v-if="round?.heldView">
                <figcaption class="caption">
                  Đeo phụ kiện cầm tay<template v-if="round.held"> · {{ round.held.skin }} · t={{ round.held.time }}s (tay giơ cao nhất)</template>
                </figcaption>
                <BlobImage :hash="round.heldView" full class="img zoom" />
              </figure>
              <figure v-for="b in images.refs" :key="b.hash">
                <figcaption class="caption">Tham chiếu{{ b.name ? ` · ${b.name}` : '' }}</figcaption>
                <BlobImage :hash="b.hash" full class="img" />
              </figure>
              <figure v-for="(b, i) in round?.strip ? [] : images.inputs" :key="b.hash">
                <figcaption class="caption">Ảnh {{ i + 1 }}{{ b.name ? ` · ${b.name}` : '' }}</figcaption>
                <BlobImage :hash="b.hash" full class="img" />
              </figure>
            </div>
            <div class="text">
              <h3>Nhận xét</h3>
              <!-- eslint-disable-next-line vue/no-v-html -- miniMarkdown escapes the input before adding tags -->
              <div class="md selectable" v-html="critiqueHtml" />
              <template v-if="round?.bounce?.length">
                <h3>Nhún hông</h3>
                <p class="caption">{{ round.bounce.length }} lần: {{ round.bounce.map(([t, d]) => `${t}s · ${d}px`).join(', ') }}</p>
              </template>
              <h3>Spec thay đổi <span class="caption">({{ round?.diff.length ?? 0 }})</span></h3>
              <p v-if="!round?.diff.length" class="caption">Không có thay đổi{{ round?.spec ? '' : ' (không có spec cũ để so)' }}.</p>
              <div v-else class="diff-wrap">
                <table class="diff">
                  <thead>
                    <tr><th>Khoá</th><th>Trước</th><th>Sau</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="d in round.diff" :key="d.path">
                      <td class="mono">{{ d.path }}</td>
                      <td class="mono old">{{ fmt(d.old) }}</td>
                      <td class="mono new">{{ fmt(d.new) }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 60;
  display: grid;
  place-items: center;
  padding: 24px 16px;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(6px);
}
.panel {
  width: min(1200px, 100%);
  max-height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: var(--r-lg, 14px);
  background: var(--bg-content);
  box-shadow: var(--shadow-lg, 0 20px 60px rgba(0, 0, 0, 0.35));
}
.head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 18px;
  border-bottom: 0.5px solid var(--separator);
}
.head h2 {
  margin: 0;
  font-size: var(--fs-title3, 17px);
}
.step {
  font-family: var(--font-mono, ui-monospace, monospace);
}
.spacer {
  flex: 1;
}
.rounds {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  padding: 10px 18px 0;
}
.rounds button {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 0.5px solid var(--separator-strong);
  border-radius: 999px;
  background: none;
  color: var(--text-2);
  font: inherit;
  cursor: pointer;
}
.rounds button.on {
  border-color: var(--accent);
  color: var(--text-1);
  background: var(--accent-soft);
}
.body {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: 18px;
  padding: 14px 18px 18px;
  overflow: auto;
}
@media (max-width: 860px) {
  .body {
    grid-template-columns: minmax(0, 1fr);
  }
}
.pics {
  display: grid;
  gap: 12px;
  align-content: start;
  min-width: 0;
}
.pics figure {
  margin: 0;
  display: grid;
  gap: 4px;
}
.img {
  width: 100%;
  border-radius: var(--r-sm);
  box-shadow: var(--hairline);
}
.text {
  min-width: 0;
}
.zoom {
  width: min(100%, 320px);
}
.text h3 {
  margin: 0 0 6px;
  font-size: var(--fs-callout);
}
.text h3 + * {
  margin-bottom: 16px;
}
.md {
  font-size: var(--fs-callout);
  line-height: 1.5;
  color: var(--text-1);
}
.md :deep(p),
.md :deep(ul),
.md :deep(ol) {
  margin: 0 0 8px;
}
.md :deep(ul),
.md :deep(ol) {
  padding-left: 20px;
}
.md :deep(h4) {
  margin: 10px 0 4px;
  font-size: var(--fs-callout);
}
.md :deep(code) {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.92em;
  padding: 0 3px;
  border-radius: 4px;
  background: var(--bg-sunken);
}
.diff-wrap {
  max-height: 340px;
  overflow: auto;
  border-radius: var(--r-sm);
  box-shadow: var(--hairline);
}
.diff {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-caption);
}
.diff th,
.diff td {
  padding: 4px 8px;
  text-align: left;
  vertical-align: top;
  border-bottom: 0.5px solid var(--separator);
  word-break: break-word;
}
.diff th {
  position: sticky;
  top: 0;
  background: var(--bg-sunken);
}
.old {
  color: var(--st-fail);
}
.new {
  color: var(--st-ok);
}
.mono {
  font-family: var(--font-mono, ui-monospace, monospace);
}
.state {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 24px 18px;
}
.err {
  padding: 18px;
  color: var(--st-fail);
}
.warn {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin: 10px 18px 0;
  padding: 8px 10px;
  border-radius: var(--r-sm);
  background: var(--st-waiting-soft);
  color: var(--st-waiting);
  font-size: var(--fs-callout);
}
.warn svg {
  flex: none;
  margin-top: 2px;
}
.caption.bad {
  color: var(--st-fail);
}
.spin {
  animation: spin 1s linear infinite;
}
.small {
  font-size: var(--fs-caption);
}
</style>

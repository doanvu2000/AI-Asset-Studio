<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useEventListener } from '@vueuse/core'
import { Download, LoaderCircle, X } from '@lucide/vue'
import { NO_PROJECT, type BlobRef, type ImportGroup } from '@/api/types'
import ProjectSelect from './ProjectSelect.vue'
import { api } from '@/stores/connection'
import { useProjects } from '@/stores/projects'
import { useToasts } from '@/stores/toasts'
import { useFocusTrap } from '@/composables/focusTrap'

const props = defineProps<{ open: boolean; paths?: string[]; blobs?: BlobRef[]; projectId?: string }>()
const emit = defineEmits<{ close: [] }>()

const toasts = useToasts()
const projects = useProjects()
const root = ref<HTMLElement | null>(null)
const closeBtn = ref<HTMLButtonElement | null>(null)
const isOpen = computed(() => props.open)
const trap = useFocusTrap(root, isOpen, closeBtn)
const project = ref(NO_PROJECT)
const approve = ref(true)
const group = ref<ImportGroup>('per_file')
const assetName = ref('')
const busy = ref(false)
const error = ref<string | null>(null)

const SHOWN = 5
const nameOf = (p: string): string => p.split(/[\\/]/).pop() ?? p
const names = computed(() => [...(props.paths ?? []).map(nameOf), ...(props.blobs ?? []).map((b) => b.name || `${b.hash.slice(0, 12)}…`)])
const total = computed(() => names.value.length)
const shown = computed(() => names.value.slice(0, SHOWN))

watch(
  () => props.open,
  (on) => {
    if (!on) return
    error.value = null
    group.value = 'per_file'
    assetName.value = ''
    project.value = props.projectId && props.projectId !== NO_PROJECT ? props.projectId : (projects.activeId ?? NO_PROJECT)
  },
  { immediate: true }
)

async function submit(): Promise<void> {
  if (busy.value || !total.value) return
  busy.value = true
  error.value = null
  try {
    const many = total.value > 1
    const res = await api().importToLibrary({
      paths: props.paths?.length ? props.paths : undefined,
      hashes: props.blobs?.length ? props.blobs.map((b) => b.hash) : undefined,
      project_id: project.value,
      approve: approve.value,
      group: many ? group.value : 'single',
      asset_name: many && group.value === 'single' ? assetName.value.trim() || undefined : undefined
    })
    const assets = res.items.length
    const files = res.items.reduce((n, i) => n + i.versions.length, 0)
    const what = assets > 1 ? `${assets} asset` : files > 1 ? `1 asset (${files} phiên bản)` : '1 asset'
    toasts.push(`Đã nhập ${what} vào thư viện${approve.value ? ' (đã duyệt)' : ''}`, 'success', 8000, {
      label: 'Mở Thư viện',
      to: { path: '/gallery', query: { project: res.project_id, run: res.run_id, focus: res.versions[0]?.version_id } }
    })
    emit('close')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

useEventListener(
  document,
  'keydown',
  (e: KeyboardEvent) => {
    if (!props.open) return
    if (trap.onTab(e)) return
    if (e.key !== 'Escape' || busy.value) return
    e.preventDefault()
    e.stopPropagation()
    emit('close')
  },
  { capture: true }
)
</script>

<template>
  <Teleport to="body">
    <Transition name="li">
      <div v-if="open" ref="root" class="scrim" role="dialog" aria-modal="true" aria-labelledby="li-title" @click.self="!busy && emit('close')">
        <section class="sheet">
          <header class="head">
            <Download class="ico" aria-hidden="true" />
            <div class="who">
              <h2 id="li-title" class="headline">Nhập vào thư viện</h2>
              <span class="caption">{{ total }} file thành phiên bản trong dự án.</span>
            </div>
            <button ref="closeBtn" type="button" class="btn btn-plain btn-icon" aria-label="Đóng (Esc)" title="Đóng (Esc)" :disabled="busy" @click="emit('close')"><X /></button>
          </header>
          <div class="body">
            <ul class="files mono caption">
              <li v-for="(n, i) in shown" :key="i" :title="n">{{ n }}</li>
              <li v-if="total > SHOWN">… và {{ total - SHOWN }} file nữa</li>
            </ul>
            <div class="row">
              <span class="section-label">Dự án</span>
              <ProjectSelect v-model="project" label="Dự án nhận file" />
            </div>
            <fieldset v-if="total > 1" class="grp" :disabled="busy">
              <legend class="section-label">Cách nhập</legend>
              <label class="chk"><input v-model="group" type="radio" value="per_file" name="li-group" /> Mỗi tệp một asset</label>
              <label class="chk"><input v-model="group" type="radio" value="single" name="li-group" /> Gộp thành 1 asset (nhiều phiên bản)</label>
              <input v-if="group === 'single'" v-model="assetName" class="field" type="text" maxlength="200" spellcheck="false" placeholder="Tên asset (để trống: lấy theo tệp đầu)" aria-label="Tên asset" />
            </fieldset>
            <label class="chk"><input v-model="approve" type="checkbox" /> Đánh dấu đã duyệt (đóng gói, QA, tìm tương tự thấy ngay)</label>
            <p v-if="error" class="caption err" role="alert">{{ error }}</p>
          </div>
          <footer class="foot">
            <button type="button" class="btn" :disabled="busy" @click="emit('close')">Huỷ</button>
            <button type="button" class="btn btn-primary" :disabled="busy || !total" @click="submit">
              <LoaderCircle v-if="busy" class="spin" /><Download v-else /> Nhập {{ total }} file
            </button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.scrim {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.32);
}
.sheet {
  width: min(460px, 100%);
  max-height: 100%;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  background: var(--bg-elevated);
  border-radius: var(--r-xl);
  box-shadow: var(--shadow-3);
  overflow: hidden;
}
.head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 10px 12px 16px;
  border-bottom: 1px solid var(--hair-soft);
}
.ico {
  color: var(--accent);
  flex: none;
}
.who {
  flex: 1;
  min-width: 0;
  display: grid;
}
.body {
  overflow: auto;
  padding: var(--sp-4) var(--sp-5);
  display: grid;
  gap: var(--sp-3);
  align-content: start;
}
.files {
  margin: 0;
  padding: 8px 10px;
  list-style: none;
  border-radius: var(--r-md);
  background: var(--bg-sunken, var(--bg));
  display: grid;
  gap: 2px;
}
.files li {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
}
.row > .section-label {
  flex: 0 0 56px;
}
.chk {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-callout);
}
.grp {
  display: grid;
  gap: 6px;
  margin: 0;
  padding: 0;
  border: 0;
  min-width: 0;
}
.grp legend {
  padding: 0;
  margin-bottom: 2px;
}
.err {
  color: var(--st-fail);
}
.foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 16px;
  border-top: 1px solid var(--hair-soft);
}
.spin {
  animation: spin 1s linear infinite;
}
.li-enter-active,
.li-leave-active {
  transition: opacity var(--t-base) var(--ease-out);
}
.li-enter-active .sheet {
  transition: transform var(--t-slow) var(--ease-spring);
}
.li-leave-active .sheet {
  transition: transform var(--t-fast) var(--ease-in);
}
.li-enter-from,
.li-leave-to {
  opacity: 0;
}
.li-enter-from .sheet,
.li-leave-to .sheet {
  transform: scale(0.97);
}
@media (prefers-reduced-motion: reduce) {
  .li-enter-active,
  .li-leave-active,
  .li-enter-active .sheet,
  .li-leave-active .sheet {
    transition: none;
  }
  .spin {
    animation: none;
  }
}
</style>

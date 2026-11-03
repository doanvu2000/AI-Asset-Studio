<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Check, CircleAlert, FolderOpen, LayoutGrid, LoaderCircle, ScanSearch, ShieldCheck, Undo2, Wrench } from '@lucide/vue'
import type { AuditBackup, AuditFix, AuditFixResult, AuditReport, AuditTexture } from '@/api/auditTypes'
import EmptyState from '@/components/EmptyState.vue'
import PageHeader from '@/components/PageHeader.vue'
import ScanProgressBar from '@/components/ScanProgressBar.vue'
import TweenNumber from '@/components/TweenNumber.vue'
import { useScanProgress } from '@/composables/scanProgress'
import { api, useConnection } from '@/stores/connection'
import { useToasts } from '@/stores/toasts'
import { bytes } from '@/composables/format'

type Tab = 'textures' | 'duplicates' | 'unreferenced' | 'meshes' | 'audio'
const FOLDER_KEY = 'audit.folder'
const MB = 1048576
const KIND_LABEL: Record<string, string> = { sprite: 'Sprite', ui: 'UI', texture: 'Texture 3D', tileset: 'Tileset', normal: 'Normal', pixel: 'Pixel art' }
const FIX_LABEL: Record<string, string> = { mobile_preset: 'Nén mobile', max_size: 'Giảm max size', drop_alpha: 'Bỏ alpha' }
const REF_LABEL: Record<string, string> = { texture: 'Ảnh', audio: 'Âm thanh', model: 'Mesh', material: 'Material', prefab: 'Prefab / scene' }

const route = useRoute()
const conn = useConnection()
const toasts = useToasts()
const scanLive = useScanProgress('audit')

function readFolder(): string {
  try {
    return localStorage.getItem(FOLDER_KEY) ?? ''
  } catch {
    return ''
  }
}

const folder = ref(typeof route.query.folder === 'string' ? route.query.folder : readFolder())
const triBudget = ref(15000)
const scanning = ref(false)
const scanError = ref<string | null>(null)
const report = ref<AuditReport | null>(null)
const tab = ref<Tab>('textures')
const picked = ref<Set<string>>(new Set())
const plan = ref<AuditFixResult | null>(null)
const planning = ref(false)
const applying = ref(false)
const backups = ref<AuditBackup[]>([])
const undoing = ref<string | null>(null)
const atlasBusy = ref(false)
const atlasOut = ref<string | null>(null)

const canScan = computed(() => /^([a-zA-Z]:[\\/]|\/)/.test(folder.value.trim()) && !scanning.value)

async function pickFolder(): Promise<void> {
  if (!window.studio) return void toasts.push('Chọn thư mục chỉ có trong ứng dụng desktop; dán đường dẫn vào ô', 'info')
  let p: string | null
  try {
    p = await window.studio.pickPath({ kind: 'dir', title: 'Chọn project Unity / Godot (hoặc thư mục con)', defaultPath: folder.value || undefined })
  } catch (e) {
    return void toasts.error(e, 'Không mở được hộp chọn thư mục')
  }
  if (p) {
    folder.value = p
    void doScan()
  }
}

function setReport(r: AuditReport): void {
  report.value = r
  picked.value = new Set(r.textures.filter((t) => t.issues.some((i) => i.fix)).map((t) => t.rel))
  plan.value = null
}

async function doScan(): Promise<void> {
  if (!canScan.value || scanning.value) return
  scanning.value = true
  scanError.value = null
  try {
    setReport(await api().audit({ path: folder.value.trim(), tri_budget: triBudget.value }))
    atlasOut.value = null
    try {
      localStorage.setItem(FOLDER_KEY, folder.value.trim())
    } catch {
      /* per-viewer convenience only */
    }
    await loadBackups()
  } catch (e) {
    if ((e as { code?: string }).code === 'cancelled') return void toasts.push('Đã huỷ quét', 'info', 2400)
    report.value = null
    scanError.value = e instanceof Error ? e.message : String(e)
  } finally {
    scanning.value = false
  }
}

async function loadBackups(): Promise<void> {
  if (!report.value || !conn.transport) return void (backups.value = [])
  try {
    backups.value = (await api().auditBackups(report.value.project)).backups
  } catch {
    backups.value = []
  }
}
watch(() => conn.transport, loadBackups)

const s = computed(() => report.value?.summary)
const fixRows = computed(() => (report.value?.textures ?? []).filter((t) => t.issues.length))
const fixable = computed(() => fixRows.value.filter((t) => t.issues.some((i) => i.fix)))
const pickedRows = computed(() => fixable.value.filter((t) => picked.value.has(t.rel)))
const savedPicked = computed(() => pickedRows.value.reduce((a, t) => a + (t.gpu_before - t.gpu_after), 0))
const audioIssues = computed(() => (report.value?.audio ?? []).filter((a) => a.issues.length))
const scannedFiles = computed(() => (s.value ? s.value.textures + s.value.meshes + s.value.audio : 0))
const clean = computed(
  () => !!report.value && !fixRows.value.length && !report.value.duplicates.length && !report.value.unreferenced.length && !s.value?.meshes_over && !audioIssues.value.length
)
const tabs = computed<{ id: Tab; label: string; n: number }[]>(() => [
  { id: 'textures', label: 'Texture', n: fixRows.value.length },
  { id: 'duplicates', label: 'Ảnh trùng', n: report.value?.duplicates.length ?? 0 },
  { id: 'unreferenced', label: 'Không ai dùng', n: report.value?.unreferenced.length ?? 0 },
  { id: 'meshes', label: 'Mesh', n: s.value?.meshes_over ?? 0 },
  { id: 'audio', label: 'Âm thanh', n: audioIssues.value.length }
])

function toggle(rel: string): void {
  const next = new Set(picked.value)
  if (next.has(rel)) next.delete(rel)
  else next.add(rel)
  picked.value = next
  plan.value = null
}
function toggleAll(): void {
  picked.value = pickedRows.value.length === fixable.value.length ? new Set() : new Set(fixable.value.map((t) => t.rel))
  plan.value = null
}

function actions(): AuditFix[] {
  return pickedRows.value.flatMap((t) => t.issues.map((i) => i.fix).filter((f): f is AuditFix => !!f))
}

async function preview(): Promise<void> {
  if (!report.value || planning.value || !pickedRows.value.length) return
  planning.value = true
  try {
    plan.value = await api().auditFix({ path: report.value.project, actions: actions(), dry_run: true })
  } catch (e) {
    toasts.error(e, 'Không lập được kế hoạch sửa')
  } finally {
    planning.value = false
  }
}

async function apply(): Promise<void> {
  if (!report.value || applying.value) return
  applying.value = true
  try {
    const r = await api().auditFix({ path: report.value.project, actions: actions(), dry_run: false })
    toasts.push(`Đã ghi ${r.written ?? 0} file import${r.skipped ? `, bỏ qua ${r.skipped}` : ''} · backup ${r.backup_id ?? ''}`, r.written ? 'success' : 'info')
    if (r.report) setReport(r.report)
    else plan.value = null
    await loadBackups()
  } catch (e) {
    toasts.error(e, 'Không sửa được')
  } finally {
    applying.value = false
  }
}

async function undo(id: string): Promise<void> {
  if (undoing.value) return
  undoing.value = id
  try {
    const r = await api().auditUndo(id)
    toasts.push(`Đã khôi phục ${r.restored.length} file${r.conflicts.length ? `; ${r.conflicts.length} file đã bị sửa sau đó nên giữ nguyên` : ''}`, r.conflicts.length ? 'info' : 'success')
    if (report.value) folder.value = report.value.root
    await doScan()
  } catch (e) {
    toasts.error(e, 'Không hoàn tác được')
  } finally {
    undoing.value = null
  }
}

const atlasRels = computed(() => pickedRows.value.filter((t) => t.kind === 'sprite' || t.kind === 'ui' || t.kind === 'pixel').map((t) => t.rel))
async function makeAtlas(): Promise<void> {
  if (!report.value || atlasBusy.value || atlasRels.value.length < 2) return
  atlasBusy.value = true
  try {
    const r = await api().auditFix({ path: report.value.project, actions: [{ type: 'atlas', rels: atlasRels.value, name: 'audit_atlas' }], dry_run: false })
    atlasOut.value = r.atlases?.[0]?.folder ?? null
    toasts.push('Đã tạo atlas ở thư mục riêng (project không đổi)', 'success')
  } catch (e) {
    toasts.error(e, 'Không tạo được atlas')
  } finally {
    atlasBusy.value = false
  }
}

function mb(n: number): string {
  return `${(n / MB).toFixed(n >= 100 * MB ? 0 : 1)} MB`
}
function size(t: AuditTexture): string {
  return `${t.w}×${t.h}`
}
function reveal(p: string): void {
  void window.studio?.revealPath(p)
}
</script>

<template>
  <div class="view" @keydown.ctrl.enter.prevent="doScan" @keydown.meta.enter.prevent="doScan">
    <PageHeader title="Kiểm tra project" subtitle="Soát texture, ảnh trùng, asset không ai dùng, mesh nặng, âm thanh của project Unity / Godot" />
    <div class="scroll">
      <div class="stack wrap">
        <section class="card pane">
          <div class="pane-head">
            <FolderOpen class="pi" aria-hidden="true" />
            <h2 class="headline">Project</h2>
            <span v-if="report" class="tag" :class="`e-${report.engine}`">{{ report.engine === 'unity' ? 'Unity' : 'Godot' }}</span>
            <span v-if="report" class="caption num push">quét {{ report.seconds }} s</span>
          </div>
          <div class="folder-row">
            <input
              v-model="folder"
              class="field mono"
              spellcheck="false"
              placeholder="L:\Game\MyProject  (Unity hoặc Godot)"
              title="Thư mục gốc project: có Assets + ProjectSettings (Unity) hoặc project.godot (Godot)"
              aria-label="Đường dẫn project"
              @keydown.enter.exact="doScan"
            />
            <button type="button" class="btn" @click="pickFolder"><FolderOpen /> Chọn…</button>
            <button type="button" class="btn btn-primary" :disabled="!canScan" @click="doScan">
              <LoaderCircle v-if="scanning" class="spin" /><ScanSearch v-else /> Quét
            </button>
          </div>
          <label class="inline caption">
            Ngân sách mesh mobile
            <input v-model.number="triBudget" type="number" min="100" step="1000" class="field num tri" aria-label="Số tam giác tối đa mỗi mesh" />
            tam giác
          </label>
          <span class="caption safe"><ShieldCheck :size="14" /> Quét chỉ đọc. Sửa chỉ ghi .meta / .import (có backup zip + hoàn tác); không xoá hay ghi đè ảnh.</span>
        </section>

        <section v-if="!report" class="card pane" aria-live="polite">
          <EmptyState v-if="scanning" :icon="ScanSearch" title="Đang quét project…" text="Đọc texture, mesh, âm thanh và tham chiếu trong project. Project lớn có thể mất vài chục giây.">
            <ScanProgressBar :progress="scanLive.progress.value" :cancelling="scanLive.cancelling.value" @cancel="scanLive.cancel" />
          </EmptyState>
          <EmptyState v-else-if="scanError" :icon="CircleAlert" title="Không quét được project" :text="scanError">
            <button type="button" class="btn" :disabled="!canScan" @click="doScan"><ScanSearch /> Quét lại</button>
          </EmptyState>
          <EmptyState v-else :icon="FolderOpen" title="Chưa chọn project" text="Dán đường dẫn project Unity hoặc Godot vào ô trên, hoặc chọn thư mục, rồi bấm Quét.">
            <button type="button" class="btn btn-primary" @click="pickFolder"><FolderOpen /> Chọn thư mục…</button>
          </EmptyState>
        </section>

        <section v-if="report && scanning" class="card pane rescan" aria-live="polite">
          <ScanProgressBar :progress="scanLive.progress.value" :cancelling="scanLive.cancelling.value" @cancel="scanLive.cancel" />
        </section>

        <Transition name="fade-up">
          <section v-if="report && s" class="card pane">
            <div class="tiles">
              <div class="tile main">
                <span class="caption">RAM GPU texture (mobile, ước tính)</span>
                <span class="big"><TweenNumber :value="s.gpu_before / MB" :decimals="1" /> → <TweenNumber :value="s.gpu_after / MB" :decimals="1" /> MB</span>
                <span class="caption">{{ s.textures }} ảnh · {{ bytes(s.texture_bytes) }} trên đĩa</span>
              </div>
              <div class="tile">
                <span class="big"><TweenNumber :value="s.fixable" /></span>
                <span class="caption">ảnh sửa 1 chạm</span>
              </div>
              <div class="tile">
                <span class="big"><TweenNumber :value="s.duplicate_groups" /></span>
                <span class="caption">nhóm trùng · {{ mb(s.duplicate_bytes) }}</span>
              </div>
              <div class="tile">
                <span class="big"><TweenNumber :value="s.unreferenced" /></span>
                <span class="caption">không ai dùng · {{ mb(s.unreferenced_bytes) }}</span>
              </div>
              <div class="tile" :class="{ warn: s.meshes_over }">
                <span class="big"><TweenNumber :value="s.meshes_over" /></span>
                <span class="caption">mesh &gt; {{ report.tri_budget.toLocaleString('vi-VN') }} tam giác</span>
              </div>
            </div>
            <p v-for="n in report.notes" :key="n" class="caption note">{{ n }}</p>

            <EmptyState v-if="clean" :icon="Check" title="Không có vấn đề nào" :text="`Đã quét ${scannedFiles.toLocaleString('vi-VN')} tệp (ảnh, mesh, âm thanh): không có texture cần sửa, ảnh trùng, asset thừa hay mesh quá nặng.`" />
            <template v-else>
            <div class="seg" role="tablist" aria-label="Nhóm vấn đề">
              <button v-for="t in tabs" :key="t.id" type="button" role="tab" :aria-selected="tab === t.id" @click="tab = t.id">
                {{ t.label }} <span class="num cnt">{{ t.n }}</span>
              </button>
            </div>

            <template v-if="tab === 'textures'">
              <div v-if="fixRows.length" class="table" role="table" aria-label="Texture có vấn đề">
                <div class="tr th" role="row">
                  <span role="columnheader">
                    <input type="checkbox" :checked="pickedRows.length === fixable.length && fixable.length > 0" :indeterminate="pickedRows.length > 0 && pickedRows.length < fixable.length" aria-label="Chọn tất cả" @change="toggleAll" />
                  </span>
                  <span role="columnheader">Ảnh</span><span role="columnheader">Vấn đề</span><span role="columnheader" class="r">RAM GPU</span>
                </div>
                <TransitionGroup name="row" tag="div" class="tbody">
                  <label v-for="t in fixRows" :key="t.rel" class="tr" role="row">
                    <span><input v-if="t.issues.some((i) => i.fix)" type="checkbox" :checked="picked.has(t.rel)" :aria-label="`Sửa ${t.rel}`" @change="toggle(t.rel)" /></span>
                    <span class="meta">
                      <span class="mono rel">{{ t.rel }}</span>
                      <span class="caption">{{ size(t) }} · {{ t.kind ? KIND_LABEL[t.kind] ?? t.kind : 'không đổi' }}<template v-if="t.display"> · hiển thị {{ t.display[0] }}×{{ t.display[1] }}</template></span>
                    </span>
                    <span class="issues">
                      <span v-for="(i, k) in t.issues" :key="k" class="caption" :class="i.severity">
                        <strong v-if="i.fix">{{ FIX_LABEL[i.fix.type] }}:</strong> {{ i.msg }}
                      </span>
                    </span>
                    <span class="r num caption">{{ mb(t.gpu_before) }}<template v-if="t.gpu_after < t.gpu_before"> → <strong class="ok-t">{{ mb(t.gpu_after) }}</strong></template></span>
                  </label>
                </TransitionGroup>
              </div>
              <p v-else class="callout ok"><Check :size="14" /> Không có texture nào cần sửa.</p>

              <div v-if="fixable.length" class="fixbar">
                <button type="button" class="btn btn-primary" :disabled="!pickedRows.length || planning" @click="preview">
                  <LoaderCircle v-if="planning" class="spin" /><Wrench v-else /> Xem trước sửa {{ pickedRows.length }} ảnh
                </button>
                <span class="caption num">tiết kiệm ~{{ mb(savedPicked) }} RAM GPU</span>
                <button type="button" class="btn push" :disabled="atlasRels.length < 2 || atlasBusy" title="Gộp các sprite đã chọn vào atlas, ghi ra thư mục riêng ngoài project; không đổi tham chiếu" @click="makeAtlas">
                  <LoaderCircle v-if="atlasBusy" class="spin" /><LayoutGrid v-else /> Tạo atlas ({{ atlasRels.length }})
                </button>
              </div>
              <p v-if="atlasOut" class="callout caption">
                Atlas ở <button type="button" class="link mono" @click="reveal(atlasOut)">{{ atlasOut }}</button> — chép vào project nếu muốn, rồi tự đổi tham chiếu sang sprite trong atlas.
              </p>
              <Transition name="fade-up">
                <div v-if="plan" class="confirm callout" role="alertdialog" aria-label="Xác nhận sửa">
                  <span>
                    Sẽ ghi <strong class="num">{{ plan.files?.filter((f) => f.action === 'write').length ?? 0 }}</strong> file import · RAM GPU
                    <strong class="num">{{ mb(plan.gpu_before ?? 0) }} → {{ mb(plan.gpu_after ?? 0) }}</strong>. File gốc nén vào backup zip trước.
                  </span>
                  <button type="button" class="btn btn-primary" :disabled="applying || !plan.files?.some((f) => f.action === 'write')" @click="apply">
                    <LoaderCircle v-if="applying" class="spin" /><Check v-else /> Ghi
                  </button>
                  <button type="button" class="btn" :disabled="applying" @click="plan = null">Huỷ</button>
                  <ul v-if="plan.files?.some((f) => f.action === 'skip')" class="caption mono skipped">
                    <li v-for="f in plan.files.filter((x) => x.action === 'skip')" :key="f.rel">{{ f.rel }} — {{ f.why }}</li>
                  </ul>
                </div>
              </Transition>
            </template>

            <ul v-else-if="tab === 'duplicates'" class="list">
              <li v-for="(g, k) in report.duplicates" :key="k" class="item">
                <span class="caption">{{ g.kind === 'exact' ? 'Trùng hệt' : g.method === 'clip' ? 'Gần giống (CLIP)' : 'Gần giống' }} · lãng phí {{ mb(g.wasted_bytes) }}</span>
                <span v-for="f in g.files" :key="f" class="mono rel">{{ f }}</span>
              </li>
              <li v-if="!report.duplicates.length" class="caption">Không có ảnh trùng.</li>
              <li v-else class="caption note">Chỉ báo; giữ 1 bản rồi trỏ tham chiếu về bản đó trong editor — app không tự xoá.</li>
            </ul>

            <ul v-else-if="tab === 'unreferenced'" class="list">
              <li v-for="u in report.unreferenced" :key="u.rel" class="item row-i">
                <span class="mono rel">{{ u.rel }}</span>
                <span class="caption">{{ REF_LABEL[u.kind] ?? u.kind }} · {{ bytes(u.bytes) }}</span>
              </li>
              <li v-if="!report.unreferenced.length" class="caption">Mọi asset đều có chỗ dùng.</li>
              <li v-else class="caption note">“Có thể” không dùng: không thấy GUID / đường dẫn trong scene, prefab, material… Asset nạp bằng code (Resources, Addressables, load theo tên) vẫn hiện ở đây — kiểm trước khi xoá.</li>
            </ul>

            <ul v-else-if="tab === 'meshes'" class="list">
              <li v-for="m in report.meshes" :key="m.rel" class="item row-i" :class="{ over: m.over }">
                <span class="mono rel">{{ m.rel }}</span>
                <span class="caption num">{{ m.tris !== null ? `${m.tris.toLocaleString('vi-VN')} tam giác` : m.error }} · {{ bytes(m.bytes) }}</span>
              </li>
              <li v-if="!report.meshes.length" class="caption">Không có mesh .fbx / .glb / .gltf / .obj.</li>
            </ul>

            <ul v-else class="list">
              <li v-for="a in audioIssues" :key="a.rel" class="item">
                <span class="mono rel">{{ a.rel }}</span>
                <span v-for="(i, k) in a.issues" :key="k" class="caption warn">{{ i.msg }}</span>
              </li>
              <li v-if="!audioIssues.length" class="caption">Thiết lập import âm thanh ổn ({{ report.audio.length }} clip).</li>
              <li v-else class="caption note">Chỉ báo; đổi trong Inspector (Unity) / Import dock (Godot).</li>
            </ul>
            </template>
          </section>
        </Transition>

        <section v-if="backups.length" class="card pane">
          <div class="pane-head"><Undo2 class="pi" aria-hidden="true" /><h2 class="headline">Lần sửa trước</h2></div>
          <div v-for="b in backups" :key="b.backup_id" class="backup caption">
            <span class="mono">{{ b.backup_id }}</span>
            <span>{{ b.written }} file</span>
            <span v-if="b.gpu_before" class="num">{{ mb(b.gpu_before) }} → {{ mb(b.gpu_after ?? 0) }}</span>
            <span v-if="b.undone_at">đã hoàn tác</span>
            <button v-else type="button" class="btn" :disabled="undoing === b.backup_id" @click="undo(b.backup_id)">
              <LoaderCircle v-if="undoing === b.backup_id" class="spin" /><Undo2 v-else /> Hoàn tác
            </button>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wrap {
  max-width: 1080px;
}
.pane.rescan {
  padding-block: var(--sp-3);
}
.pane {
  display: grid;
  gap: var(--sp-3);
  padding: var(--sp-4);
}
.pane-head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.pi {
  width: 16px;
  height: 16px;
  color: var(--accent);
}
.push {
  margin-left: auto;
}
.tag {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: var(--fs-caption);
  font-weight: 600;
  background: var(--bg-active);
}
.e-unity {
  background: var(--accent-soft);
  color: var(--accent);
}
.e-godot {
  background: var(--st-cached-soft);
  color: var(--st-cached);
}
.folder-row {
  display: flex;
  gap: var(--sp-2);
}
.folder-row .field {
  flex: 1;
}
.inline {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.tri {
  width: 96px;
}
.safe {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--st-ok);
}
.tiles {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
}
.tile {
  display: grid;
  align-content: start;
  min-width: 110px;
  padding: 10px 12px;
  border-radius: var(--r-md);
  background: var(--bg-content);
  box-shadow: var(--hairline);
}
.tile.main {
  flex: 1 1 260px;
  background: var(--accent-soft);
}
.tile.warn .big {
  color: var(--st-human);
}
.big {
  font-size: var(--fs-title1);
  font-weight: 650;
  letter-spacing: -0.02em;
}
.note {
  color: var(--text-3);
  margin: 0;
}
.cnt {
  margin-left: 4px;
  opacity: 0.7;
}
.table {
  display: grid;
  max-height: 460px;
  overflow: auto;
  border-radius: var(--r-md);
  box-shadow: var(--hairline);
}
.tbody {
  display: grid;
}
.tr {
  display: grid;
  grid-template-columns: 28px minmax(200px, 1.1fr) 2fr 150px;
  gap: var(--sp-2);
  align-items: start;
  padding: 8px 10px;
  border-top: 1px solid var(--separator);
  cursor: default;
}
.th {
  position: sticky;
  top: 0;
  z-index: 1;
  border-top: 0;
  background: var(--bg-content);
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--text-2);
}
.meta,
.issues {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.rel {
  overflow-wrap: anywhere;
  font-size: var(--fs-callout);
}
.warn {
  color: var(--st-human);
}
.info {
  color: var(--text-2);
}
.r {
  text-align: right;
}
.ok-t {
  color: var(--st-ok);
}
.fixbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
}
.confirm {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
}
.skipped {
  flex-basis: 100%;
  max-height: 140px;
  overflow: auto;
  margin: 0;
  padding-left: 18px;
}
.link {
  border: 0;
  padding: 0;
  background: none;
  color: var(--accent);
  cursor: pointer;
}
.list {
  display: grid;
  gap: 6px;
  max-height: 460px;
  overflow: auto;
  margin: 0;
  padding: 0;
  list-style: none;
}
.item {
  display: grid;
  gap: 2px;
  padding: 8px 10px;
  border-radius: var(--r-md);
  background: var(--bg-content);
  box-shadow: var(--hairline);
}
.row-i {
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
}
.item.over .caption {
  color: var(--st-human);
  font-weight: 600;
}
.backup {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
}
.callout.ok {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--st-ok);
}
</style>

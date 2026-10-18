<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { CircleAlert, Plus, Trash2, TriangleAlert, X } from '@lucide/vue'
import type { KindInfo, RecipeDoc, RecipeIssue, RecipeStepDoc } from '@/api/types'
import { kindName } from '@/composables/format'
import { parts } from '@/composables/graph'
import LaneTag from '../LaneTag.vue'
import ParamField from './ParamField.vue'

const props = defineProps<{ doc: RecipeDoc; step: RecipeStepDoc; kinds: KindInfo[]; issues: RecipeIssue[] }>()
const emit = defineEmits<{ rename: [to: string]; remove: []; changed: [] }>()

const info = computed(() => props.kinds.find((k) => k.kind === props.step.kind))
const title = computed(() => {
  const n = kindName(props.step.kind)
  return n === props.step.kind ? (info.value?.label ?? n) : n
})
const inputNames = computed(() => Object.keys(props.doc.inputs ?? {}))
const params = computed(() => props.step.params ?? {})
const spec = computed(() => info.value?.params ?? {})
const basic = computed(() => Object.entries(spec.value).filter(([, s]) => !s.advanced))
const advanced = computed(() => Object.entries(spec.value).filter(([, s]) => s.advanced))
const extra = computed(() => Object.keys(params.value).filter((k) => !(k in spec.value)))
const bindings = computed(() => Object.entries(props.step.in ?? {}))

const idDraft = ref(props.step.id)
const idError = ref<string | null>(null)
watch(
  () => props.step.id,
  (v) => {
    idDraft.value = v
    idError.value = null
  }
)
function commitId(): void {
  const v = idDraft.value.trim()
  if (v === props.step.id) return
  if (!/^[A-Za-z_][\w-]*$/.test(v) || v === 'inputs') idError.value = 'Chỉ chữ, số, _ và -; không bắt đầu bằng số'
  else if (props.doc.steps.some((s) => s.id === v)) idError.value = 'Đã có bước trùng tên'
  else {
    idError.value = null
    emit('rename', v)
  }
}

function setParam(key: string, v: unknown): void {
  const p = { ...(props.step.params ?? {}) }
  if (v === undefined) delete p[key]
  else p[key] = v
  props.step.params = p
  if (props.step.map_param && !(props.step.map_param in p)) props.step.map_param = null
  emit('changed')
}

function removePart(name: string, i: number): void {
  const b = props.step.in?.[name]
  if (!b || !props.step.in) return
  const kept = parts(b).filter((_, j) => j !== i)
  if (!kept.length) {
    delete props.step.in[name]
    if (props.step.map === name) props.step.map = null
  } else props.step.in[name] = kept.length === 1 ? kept[0]! : kept
  emit('changed')
}

function setKind(kind: string): void {
  props.step.kind = kind
  emit('changed')
}

function setMap(v: string, field: 'map' | 'map_param'): void {
  props.step[field] = v || null
  if (v) props.step[field === 'map' ? 'map_param' : 'map'] = null
  emit('changed')
}

const newKey = ref('')
function addParam(): void {
  const k = newKey.value.trim()
  if (!k || k in params.value) return
  setParam(k, '')
  newKey.value = ''
}

const confirmDelete = ref(false)
let timer: number | undefined
function remove(): void {
  if (!confirmDelete.value) {
    confirmDelete.value = true
    timer = window.setTimeout(() => (confirmDelete.value = false), 3000)
    return
  }
  window.clearTimeout(timer)
  emit('remove')
}
</script>

<template>
  <div class="insp">
    <header class="head">
      <div class="t">
        <h2 class="title">{{ title }}</h2>
        <p class="caption mono">{{ step.kind }}</p>
      </div>
      <LaneTag v-if="info" :lane="info.lane" />
    </header>

    <ul v-if="issues.length" class="issues">
      <li v-for="(i, n) in issues" :key="n" :class="i.level">
        <component :is="i.level === 'error' ? CircleAlert : TriangleAlert" :size="14" />
        <span class="selectable">{{ i.message }}</span>
      </li>
    </ul>

    <section class="grp">
      <div class="row2">
        <div class="f">
          <label for="st-id" class="lbl">Mã bước</label>
          <input id="st-id" v-model="idDraft" class="field mono" :aria-invalid="!!idError" @change="commitId" @keydown.enter="commitId" />
        </div>
        <div class="f">
          <label for="st-kind" class="lbl">Loại</label>
          <select id="st-kind" class="field" :value="step.kind" @change="setKind(($event.target as HTMLSelectElement).value)">
            <option v-if="!info" :value="step.kind">{{ step.kind }} (không rõ)</option>
            <option v-for="k in kinds" :key="k.kind" :value="k.kind">{{ k.kind }}</option>
          </select>
        </div>
      </div>
      <p v-if="idError" class="err">{{ idError }}</p>
    </section>

    <section class="grp">
      <h3 class="section-label">Đầu vào (nối từ bước khác)</h3>
      <p v-if="!bindings.length" class="caption">Kéo từ chấm bên phải một bước khác vào chấm bên trái bước này để nối.</p>
      <div v-for="[name, b] in bindings" :key="name" class="bind">
        <span class="bname mono">{{ name }}</span>
        <span class="chips">
          <span v-for="(p, i) in parts(b)" :key="p + i" class="chip mono">
            {{ p }}
            <button type="button" :aria-label="`Bỏ ${p}`" @click="removePart(name, i)"><X :size="11" /></button>
          </span>
        </span>
      </div>
      <div class="row2">
        <div class="f">
          <label for="st-map" class="lbl">Chạy lặp theo đầu vào</label>
          <select id="st-map" class="field" :value="step.map ?? ''" @change="setMap(($event.target as HTMLSelectElement).value, 'map')">
            <option value="">Không</option>
            <option v-for="[n] in bindings" :key="n" :value="n">{{ n }}</option>
          </select>
        </div>
        <div class="f">
          <label for="st-mapp" class="lbl">Chạy lặp theo tham số</label>
          <select id="st-mapp" class="field" :value="step.map_param ?? ''" @change="setMap(($event.target as HTMLSelectElement).value, 'map_param')">
            <option value="">Không</option>
            <option v-for="k in Object.keys(params)" :key="k" :value="k">{{ k }}</option>
          </select>
        </div>
      </div>
    </section>

    <section v-if="basic.length" class="grp">
      <h3 class="section-label">Tham số</h3>
      <ParamField v-for="[k, s] in basic" :key="k" :name="k" :spec="s" :value="params[k]" :inputs="inputNames" @change="setParam(k, $event)" />
    </section>

    <details v-if="advanced.length" class="grp adv">
      <summary class="section-label">Nâng cao</summary>
      <ParamField v-for="[k, s] in advanced" :key="k" :name="k" :spec="s" :value="params[k]" :inputs="inputNames" @change="setParam(k, $event)" />
    </details>

    <section class="grp">
      <h3 class="section-label">{{ info?.free_params ? 'Tham số node / khác' : 'Tham số khác' }}</h3>
      <div v-for="k in extra" :key="k" class="extra">
        <ParamField :name="k" :value="params[k]" :inputs="inputNames" @change="setParam(k, $event)" />
        <button type="button" class="btn btn-plain btn-icon del" :aria-label="`Xoá ${k}`" @click="setParam(k, undefined)"><X /></button>
      </div>
      <form class="add-param" @submit.prevent="addParam">
        <input v-model="newKey" class="field mono" placeholder="tên tham số, vd 57:3.seed" />
        <button type="submit" class="btn" :disabled="!newKey.trim()"><Plus /> Thêm</button>
      </form>
    </section>

    <button type="button" class="btn btn-danger del-step" :class="{ armed: confirmDelete }" @click="remove">
      <Trash2 />
      {{ confirmDelete ? 'Bấm lần nữa để xoá' : 'Xoá bước' }}
    </button>
  </div>
</template>

<style scoped>
.insp {
  display: grid;
  gap: 18px;
  animation: fade-up var(--t-base) var(--ease-out);
}
.head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.t {
  flex: 1;
  min-width: 0;
}
.mono {
  font-family: var(--mono);
}
.grp {
  display: grid;
  gap: 10px;
}
.row2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.f {
  display: grid;
  gap: 5px;
  min-width: 0;
}
.lbl {
  font-size: var(--fs-callout);
  font-weight: 500;
  color: var(--text-2);
}
.field {
  font-size: var(--fs-body);
  min-height: 30px;
  padding: 5px 9px;
}
select.field {
  appearance: auto;
}
.field.mono {
  font-size: 12px;
}
.field[aria-invalid='true'] {
  box-shadow: 0 0 0 1.5px var(--st-fail);
}
.err {
  font-size: var(--fs-caption);
  color: var(--st-fail);
}
.issues {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 6px;
}
.issues li {
  display: flex;
  gap: 8px;
  padding: 8px 10px;
  border-radius: var(--r-sm);
  font-size: var(--fs-callout);
}
.issues .error {
  background: var(--st-fail-soft);
}
.issues .error svg {
  color: var(--st-fail);
  flex: none;
  margin-top: 1px;
}
.issues .warning {
  background: var(--st-waiting-soft);
}
.issues .warning svg {
  color: var(--st-waiting);
  flex: none;
  margin-top: 1px;
}
.bind {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.bname {
  font-size: 12px;
  font-weight: 600;
  min-width: 52px;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 4px 0 8px;
  border-radius: 999px;
  font-size: 11px;
  background: var(--accent-soft);
  color: var(--text);
}
.chip button {
  display: grid;
  place-items: center;
  width: 16px;
  height: 16px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--text-2);
  cursor: pointer;
}
.chip button:hover {
  background: var(--bg-active);
  color: var(--st-fail);
}
.adv summary {
  cursor: pointer;
}
.adv[open] {
  gap: 12px;
}
.extra {
  display: grid;
  grid-template-columns: 1fr auto;
  align-items: end;
  gap: 4px;
}
.del {
  color: var(--text-3);
}
.add-param {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 6px;
}
.del-step {
  justify-self: start;
  margin-bottom: 12px;
}
.del-step.armed {
  background: var(--st-fail);
  color: #fff;
}
</style>

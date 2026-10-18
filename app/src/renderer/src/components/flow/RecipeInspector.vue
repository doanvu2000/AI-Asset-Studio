<script setup lang="ts">
import { computed, ref } from 'vue'
import { CircleAlert, Plus, TriangleAlert, X } from '@lucide/vue'
import type { RecipeDoc, RecipeInputSpec, RecipeIssue } from '@/api/types'
import SelectMenu, { type SelectOption } from './SelectMenu.vue'

const props = defineProps<{ doc: RecipeDoc; issues: RecipeIssue[]; idLocked: boolean }>()
const emit = defineEmits<{ changed: []; renameInput: [from: string, to: string] }>()

const TYPES: SelectOption[] = [
  { value: 'text', label: 'Chữ' },
  { value: 'int', label: 'Số nguyên' },
  { value: 'float', label: 'Số thực' },
  { value: 'bool', label: 'Bật/tắt' },
  { value: 'enum', label: 'Lựa chọn' },
  { value: 'image', label: 'Ảnh' },
  { value: 'mesh', label: 'Mesh' },
  { value: 'preset', label: 'Preset xuất' }
]
const BOOL_DEFAULTS: SelectOption[] = [
  { value: '', label: 'Không mặc định' },
  { value: 'true', label: 'Bật' },
  { value: 'false', label: 'Tắt' }
]

const inputs = computed(() => Object.entries(props.doc.inputs ?? {}))
const outputs = computed(() => Object.entries(props.doc.outputs ?? {}))
const general = computed(() => props.issues.filter((i) => !i.step))

function patch(name: string, p: Partial<RecipeInputSpec>): void {
  const cur = props.doc.inputs?.[name]
  if (!cur || !props.doc.inputs) return
  const next: RecipeInputSpec = { ...cur, ...p }
  for (const k of Object.keys(next) as Array<keyof RecipeInputSpec>) if (next[k] === undefined || next[k] === '') delete next[k]
  if (p.type && p.type !== cur.type) {
    delete next.default
    delete next.min
    delete next.max
    if (p.type !== 'enum' && p.type !== 'int' && p.type !== 'float') delete next.options
  }
  props.doc.inputs[name] = next
  emit('changed')
}

function parseDefault(spec: RecipeInputSpec, raw: string): unknown {
  if (raw.trim() === '') return undefined
  if (spec.type === 'int' || spec.type === 'float') {
    const n = Number(raw)
    return Number.isNaN(n) ? undefined : n
  }
  if (spec.type === 'bool') return raw === 'true'
  return raw
}

function parseOptions(spec: RecipeInputSpec, raw: string): Array<string | number> | undefined {
  const items = raw.split(',').map((s) => s.trim()).filter(Boolean)
  if (!items.length) return undefined
  return spec.type === 'int' || spec.type === 'float' ? items.map(Number).filter((n) => !Number.isNaN(n)) : items
}

function rename(from: string, to: string): void {
  const v = to.trim()
  if (!v || v === from || !/^[A-Za-z_][\w-]*$/.test(v) || v in (props.doc.inputs ?? {})) return
  emit('renameInput', from, v)
}

function removeInput(name: string): void {
  if (!props.doc.inputs) return
  delete props.doc.inputs[name]
  emit('changed')
}

const newName = ref('')
function addInput(): void {
  const n = newName.value.trim() || `input${Object.keys(props.doc.inputs ?? {}).length + 1}`
  if (!/^[A-Za-z_][\w-]*$/.test(n) || n in (props.doc.inputs ?? {})) return
  props.doc.inputs = { ...(props.doc.inputs ?? {}), [n]: { type: 'text', label: n } }
  newName.value = ''
  emit('changed')
}

function removeOutput(name: string): void {
  if (!props.doc.outputs) return
  delete props.doc.outputs[name]
  emit('changed')
}

function setMeta(field: 'id' | 'name' | 'description', v: string): void {
  props.doc[field] = v
  emit('changed')
}
</script>

<template>
  <div class="insp">
    <header>
      <h2 class="title">Quy trình</h2>
      <p class="caption">Chọn một bước trên sơ đồ để sửa tham số của nó.</p>
    </header>

    <ul v-if="general.length" class="issues">
      <li v-for="(i, n) in general" :key="n" :class="i.level">
        <component :is="i.level === 'error' ? CircleAlert : TriangleAlert" :size="14" />
        <span class="selectable">{{ i.message }}</span>
      </li>
    </ul>

    <section class="grp">
      <div class="f">
        <label for="r-id" class="lbl">Mã quy trình</label>
        <input id="r-id" class="field mono" :value="doc.id" :readonly="idLocked" @change="setMeta('id', ($event.target as HTMLInputElement).value.trim())" />
        <p v-if="!idLocked" class="caption">Đổi mã = lưu thành quy trình mới.</p>
      </div>
      <div class="f">
        <label for="r-name" class="lbl">Tên hiển thị</label>
        <input id="r-name" class="field" :value="doc.name ?? ''" @change="setMeta('name', ($event.target as HTMLInputElement).value)" />
      </div>
      <div class="f">
        <label for="r-desc" class="lbl">Mô tả</label>
        <textarea id="r-desc" class="field" rows="2" :value="doc.description ?? ''" @change="setMeta('description', ($event.target as HTMLTextAreaElement).value)" />
      </div>
    </section>

    <section class="grp">
      <h3 class="section-label">Đầu vào quy trình</h3>
      <div v-auto-animate class="inputs">
        <article v-for="[name, spec] in inputs" :key="name" class="inp card">
          <div class="inp-row">
            <input class="field mono nm" :value="name" :aria-label="`Tên đầu vào ${name}`" @change="rename(name, ($event.target as HTMLInputElement).value)" />
            <SelectMenu :model-value="String(spec.type ?? 'text')" :options="TYPES" :aria-label="`Kiểu của ${name}`" @update:model-value="patch(name, { type: $event })" />
            <button type="button" class="btn btn-plain btn-icon" :aria-label="`Xoá ${name}`" @click="removeInput(name)"><X /></button>
          </div>
          <div class="inp-row">
            <input class="field" placeholder="Nhãn" :value="spec.label ?? ''" @change="patch(name, { label: ($event.target as HTMLInputElement).value })" />
            <SelectMenu
              v-if="spec.type === 'bool'"
              :model-value="String(spec.default ?? '')"
              :options="BOOL_DEFAULTS"
              :aria-label="`Mặc định của ${name}`"
              @update:model-value="patch(name, { default: parseDefault(spec, $event) })"
            />
            <input
              v-else-if="spec.type !== 'image' && spec.type !== 'mesh'"
              class="field"
              placeholder="Mặc định"
              :value="spec.default === undefined ? '' : String(spec.default)"
              @change="patch(name, { default: parseDefault(spec, ($event.target as HTMLInputElement).value) })"
            />
          </div>
          <div v-if="spec.type === 'int' || spec.type === 'float'" class="inp-row three">
            <input class="field num" type="number" placeholder="Nhỏ nhất" :value="spec.min ?? ''" @change="patch(name, { min: parseDefault(spec, ($event.target as HTMLInputElement).value) as number | undefined })" />
            <input class="field num" type="number" placeholder="Lớn nhất" :value="spec.max ?? ''" @change="patch(name, { max: parseDefault(spec, ($event.target as HTMLInputElement).value) as number | undefined })" />
            <input class="field" placeholder="Lựa chọn: 64, 128" :value="(spec.options ?? []).join(', ')" @change="patch(name, { options: parseOptions(spec, ($event.target as HTMLInputElement).value) })" />
          </div>
          <input v-else-if="spec.type === 'enum'" class="field" placeholder="Lựa chọn, cách nhau bởi dấu phẩy" :value="(spec.options ?? []).join(', ')" @change="patch(name, { options: parseOptions(spec, ($event.target as HTMLInputElement).value) })" />
        </article>
      </div>
      <form class="add" @submit.prevent="addInput">
        <input v-model="newName" class="field mono" placeholder="tên đầu vào mới" />
        <button type="submit" class="btn"><Plus /> Thêm đầu vào</button>
      </form>
      <p class="caption">Ảnh / mesh nối bằng dây từ khối “Đầu vào quy trình”. Chữ / số dùng trong tham số qua nút liên kết (<span class="mono">${inputs.ten}</span>).</p>
    </section>

    <section class="grp">
      <h3 class="section-label">Đầu ra quy trình</h3>
      <p v-if="!outputs.length" class="caption">Kéo dây từ bước cuối vào khối “Đầu ra quy trình”. Đầu ra được lưu vào Thư viện.</p>
      <div v-for="[name, b] in outputs" :key="name" class="out">
        <span class="mono nm2">{{ name }}</span>
        <span class="mono caption">← {{ b }}</span>
        <button type="button" class="btn btn-plain btn-icon" :aria-label="`Xoá đầu ra ${name}`" @click="removeOutput(name)"><X /></button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.insp {
  display: grid;
  gap: 18px;
  padding-bottom: 16px;
  animation: fade-up var(--t-base) var(--ease-out);
}
.mono {
  font-family: var(--mono);
}
.grp {
  display: grid;
  gap: 10px;
}
.f {
  display: grid;
  gap: 5px;
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
  min-width: 0;
}
.field.mono {
  font-size: 12px;
}
.field[readonly] {
  color: var(--text-2);
  background: var(--bg-sunken);
}
.inputs {
  display: grid;
  gap: 8px;
}
.inp {
  display: grid;
  gap: 6px;
  padding: 8px;
  border-radius: var(--r-md);
}
.inp-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  align-items: center;
}
.inp-row:first-child {
  grid-template-columns: 1fr 120px 30px;
}
.inp-row.three {
  grid-template-columns: 1fr 1fr 1.4fr;
}
.add {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 6px;
}
.out {
  display: flex;
  align-items: center;
  gap: 8px;
}
.nm2 {
  font-weight: 600;
  font-size: 12px;
}
.out .caption {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
}
.issues .warning {
  background: var(--st-waiting-soft);
}
.issues .warning svg {
  color: var(--st-waiting);
  flex: none;
}
</style>

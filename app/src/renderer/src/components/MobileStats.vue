<script setup lang="ts">
import { computed } from 'vue'
import type { AtlasStats, MobileBudget } from '@/api/types'
import TweenNumber from './TweenNumber.vue'
import { bytes } from '@/composables/format'

const props = defineProps<{ atlas?: AtlasStats | null; budgets?: MobileBudget[] }>()

const ENGINE: Record<string, string> = { unity: 'Unity (Android/iOS)', godot: 'Godot (ETC2/ASTC)' }
const KIND: Record<string, string> = { sprite: 'Sprite', ui: 'UI / chữ', texture: 'Texture', tileset: 'Tileset', normal: 'Normal', pixel: 'Pixel art' }
const fmt = (n: number): string => bytes(Math.round(n))
const saved = (a: number, b: number): number => (a > 0 ? Math.max(0, Math.round((1 - b / a) * 100)) : 0)
const budgets = computed(() => (props.budgets ?? []).filter((b) => b.totals.files > 0))
const pages = computed(() => (props.atlas?.page_sizes ?? []).map(([w, h]) => `${w}×${h}`).join(', '))
</script>

<template>
  <div class="ms">
    <div v-if="atlas" class="blk">
      <p class="ttl">
        Atlas: <TweenNumber :value="atlas.sprites" /> sprite → <TweenNumber :value="atlas.pages" /> trang
        <span class="caption">({{ pages }}, lấp đầy <TweenNumber :value="atlas.fill * 100" />%)</span>
      </p>
      <dl class="kv">
        <dt>Texture</dt>
        <dd><TweenNumber :value="atlas.before.textures" /> → <TweenNumber :value="atlas.after.textures" /></dd>
        <dt>Tệp PNG</dt>
        <dd><TweenNumber :value="atlas.before.file_bytes" :format="fmt" /> → <TweenNumber :value="atlas.after.file_bytes" :format="fmt" /></dd>
        <dt>RAM GPU (chưa nén)</dt>
        <dd><TweenNumber :value="atlas.before.gpu_bytes" :format="fmt" /> → <TweenNumber :value="atlas.after.gpu_bytes" :format="fmt" /></dd>
      </dl>
    </div>
    <div v-for="b in budgets" :key="b.engine" class="blk">
      <p class="ttl">
        Ngân sách mobile · {{ ENGINE[b.engine] ?? b.engine }}:
        <TweenNumber :value="b.totals.gpu_before" :format="fmt" /> → <TweenNumber :value="b.totals.gpu_after" :format="fmt" />
        <span class="save">−<TweenNumber :value="saved(b.totals.gpu_before, b.totals.gpu_after)" />%</span>
      </p>
      <details v-if="b.rows.length">
        <summary class="caption">Bảng {{ b.rows.length }} ảnh</summary>
        <table class="tbl">
          <thead>
            <tr><th>Tệp</th><th>Loại</th><th>Kích thước</th><th>Nén</th><th class="r">RAM GPU</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in b.rows" :key="r.file">
              <td class="ell" :title="r.file">{{ r.file }}</td>
              <td>{{ KIND[r.kind] ?? r.kind }}</td>
              <td class="num">{{ r.import_size.join('×') }}</td>
              <td>{{ r.format }}</td>
              <td class="num r">{{ fmt(r.gpu_before) }} → {{ fmt(r.gpu_after) }}</td>
            </tr>
          </tbody>
        </table>
      </details>
    </div>
  </div>
</template>

<style scoped>
.ms {
  display: grid;
  gap: 10px;
}
.blk {
  display: grid;
  gap: 4px;
}
.ttl {
  margin: 0;
  font-weight: 600;
}
.kv {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 2px 12px;
  margin: 0;
  font-size: var(--fs-caption);
}
.kv dt {
  color: var(--text-2);
}
.kv dd {
  margin: 0;
}
.save {
  margin-left: 6px;
  color: var(--st-ok);
}
.tbl {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: var(--fs-caption);
  margin-top: 4px;
}
.tbl th,
.tbl td {
  padding: 3px 6px;
  text-align: left;
  border-bottom: 0.5px solid var(--separator);
}
.tbl th:first-child,
.tbl td:first-child {
  width: 30%;
}
.tbl .r {
  text-align: right;
  white-space: nowrap;
  width: 30%;
}
.ell {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
details summary {
  cursor: pointer;
}
</style>

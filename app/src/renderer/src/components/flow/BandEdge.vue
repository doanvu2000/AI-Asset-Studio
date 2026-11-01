<script setup lang="ts">
import { computed } from 'vue'
import { BaseEdge } from '@vue-flow/core'

const props = defineProps<{ sourceX: number; sourceY: number; targetX: number; targetY: number; data: { route: Array<[number, number]> } }>()
const R = 10

const path = computed(() => {
  const all: Array<[number, number]> = [[props.sourceX, props.sourceY], ...props.data.route, [props.targetX, props.targetY]]
  const pts = all.filter((p, i) => i === 0 || p[0] !== all[i - 1]![0] || p[1] !== all[i - 1]![1])
  let d = `M${pts[0]![0]},${pts[0]![1]}`
  for (let i = 1; i < pts.length - 1; i++) {
    const [px, py] = pts[i - 1]!
    const [cx, cy] = pts[i]!
    const [nx, ny] = pts[i + 1]!
    const r = Math.min(R, Math.hypot(cx - px, cy - py) / 2, Math.hypot(nx - cx, ny - cy) / 2)
    const inX = cx - Math.sign(cx - px) * r
    const inY = cy - Math.sign(cy - py) * r
    const outX = cx + Math.sign(nx - cx) * r
    const outY = cy + Math.sign(ny - cy) * r
    d += ` L${inX},${inY} Q${cx},${cy} ${outX},${outY}`
  }
  return `${d} L${pts[pts.length - 1]![0]},${pts[pts.length - 1]![1]}`
})
</script>

<template>
  <BaseEdge :path="path" />
</template>

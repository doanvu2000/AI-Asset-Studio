<script setup lang="ts">
import '@vue-flow/core/dist/style.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import { nextTick, watch } from 'vue'
import { VueFlow, useVueFlow, type Connection, type Edge, type EdgeMouseEvent, type Node, type NodeDragEvent, type NodeMouseEvent } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import StepNode from './StepNode.vue'
import IoNode from './IoNode.vue'
import type { IoNodeData, StepNodeData } from '@/composables/graph'

const props = withDefaults(defineProps<{ id: string; nodes: Node[]; edges: Edge[]; editable?: boolean; fitKey?: string; minimap?: boolean; focusId?: string | null }>(), {
  editable: false,
  fitKey: '',
  minimap: true,
  focusId: null
})
const emit = defineEmits<{
  connect: [c: Connection]
  nodeClick: [id: string]
  edgeClick: [id: string]
  paneClick: []
  dragStop: [positions: Array<{ id: string; x: number; y: number }>]
}>()

const { fitView, getNodes, getViewport, setViewport, setCenter, dimensions, onNodesInitialized } = useVueFlow(props.id)
const MIN_READABLE_ZOOM = 0.8

// Long linear recipes fit only at an unreadable zoom; then keep a readable zoom and start from the left end instead.
async function frame(duration = 280): Promise<void> {
  await fitView({ padding: 0.14, duration: 0, maxZoom: 1.05 })
  const v = getViewport()
  const nodes = getNodes.value
  if (v.zoom >= MIN_READABLE_ZOOM || !nodes.length) {
    if (duration) await fitView({ padding: 0.14, duration, maxZoom: 1.05 })
    return
  }
  const minX = Math.min(...nodes.map((n) => n.position.x))
  const minY = Math.min(...nodes.map((n) => n.position.y))
  const maxY = Math.max(...nodes.map((n) => n.position.y + (n.dimensions.height || 0)))
  const zoom = MIN_READABLE_ZOOM
  await setViewport({ x: 40 - minX * zoom, y: dimensions.value.height / 2 - ((minY + maxY) / 2) * zoom, zoom }, { duration })
}

watch(
  () => props.focusId,
  async (id) => {
    if (!id) return
    await nextTick()
    const n = getNodes.value.find((x) => x.id === id)
    if (!n) return
    const v = getViewport()
    const w = n.dimensions.width || 232
    const h = n.dimensions.height || 80
    const sx = n.position.x * v.zoom + v.x
    const sy = n.position.y * v.zoom + v.y
    const inside = sx >= 0 && sy >= 0 && sx + w * v.zoom <= dimensions.value.width && sy + h * v.zoom <= dimensions.value.height
    if (!inside) await setCenter(n.position.x + w / 2, n.position.y + h / 2, { zoom: v.zoom, duration: 300 })
  }
)

let framed = false
onNodesInitialized(() => {
  if (framed) return
  framed = true
  void frame(0)
})
watch(
  () => props.fitKey,
  async () => {
    await nextTick()
    requestAnimationFrame(() => void frame())
  }
)

const laneColor: Record<string, string> = {
  gpu: 'var(--lane-gpu)',
  blender: 'var(--lane-blender)',
  cloud: 'var(--lane-cloud)',
  cpu: 'var(--lane-cpu)',
  none: 'var(--lane-none)'
}
function miniColor(n: Node): string {
  return n.type === 'step' ? (laneColor[(n.data as StepNodeData).lane] ?? 'var(--text-3)') : 'var(--separator-strong)'
}

function onDragStop(e: NodeDragEvent): void {
  emit(
    'dragStop',
    e.nodes.map((n) => ({ id: n.id, x: Math.round(n.position.x), y: Math.round(n.position.y) }))
  )
}
</script>

<template>
  <div class="canvas" :class="{ editable }">
    <VueFlow
      :id="id"
      :nodes="nodes"
      :edges="edges"
      :nodes-draggable="editable"
      :nodes-connectable="editable"
      :elements-selectable="false"
      :delete-key-code="null"
      :min-zoom="0.3"
      :max-zoom="1.8"
      :default-edge-options="{ type: 'smoothstep' }"
      :connection-radius="28"
      @connect="emit('connect', $event)"
      @node-click="(e: NodeMouseEvent) => emit('nodeClick', e.node.id)"
      @edge-click="(e: EdgeMouseEvent) => emit('edgeClick', e.edge.id)"
      @pane-click="emit('paneClick')"
      @node-drag-stop="onDragStop"
    >
      <template #node-step="p">
        <StepNode :data="p.data as StepNodeData" />
      </template>
      <template #node-io="p">
        <IoNode :data="p.data as IoNodeData" />
      </template>
      <Background :gap="18" :size="1.4" pattern-color="var(--canvas-dot)" />
      <Controls :show-interactive="false" position="bottom-right" />
      <MiniMap v-if="minimap" pannable zoomable position="top-right" :width="150" :height="96" :node-color="miniColor" :node-border-radius="6" mask-color="color-mix(in srgb, var(--bg-content) 70%, transparent)" />
    </VueFlow>
  </div>
</template>

<style scoped>
.canvas {
  position: relative;
  width: 100%;
  height: 100%;
  background: var(--bg-content);
}
.canvas :deep(.vue-flow__edge-path) {
  stroke: var(--separator-strong);
  stroke-width: 1.6;
  transition: stroke var(--t-base) var(--ease-out);
}
.canvas :deep(.vue-flow__edge.bind .vue-flow__edge-path),
.canvas :deep(.vue-flow__edge.out .vue-flow__edge-path) {
  stroke: color-mix(in srgb, var(--accent) 55%, var(--separator-strong));
}
.canvas :deep(.vue-flow__edge.ref .vue-flow__edge-path) {
  stroke-dasharray: 5 4;
}
.canvas :deep(.vue-flow__edge.done .vue-flow__edge-path) {
  stroke: var(--st-ok);
}
.canvas :deep(.vue-flow__edge.flow .vue-flow__edge-path) {
  stroke: var(--st-running);
}
.canvas :deep(.vue-flow__edge.picked .vue-flow__edge-path) {
  stroke: var(--st-fail);
  stroke-width: 2.4;
}
.canvas.editable :deep(.vue-flow__edge) {
  cursor: pointer;
}
.canvas :deep(.vue-flow__edge-textbg) {
  fill: var(--bg-content);
}
.canvas :deep(.vue-flow__edge-text) {
  fill: var(--text-3);
  font-size: 10px;
  font-family: var(--mono);
}
.canvas :deep(.vue-flow__connection-path) {
  stroke: var(--accent);
  stroke-width: 2;
}
.canvas :deep(.vue-flow__node) {
  cursor: pointer;
}
.canvas.editable :deep(.vue-flow__node.dragging) {
  cursor: grabbing;
}
.canvas :deep(.vue-flow__controls) {
  border-radius: 10px;
  overflow: hidden;
  box-shadow: var(--shadow-2), var(--hairline);
}
.canvas :deep(.vue-flow__controls-button) {
  background: var(--bg-elevated);
  border-bottom: 0.5px solid var(--separator);
  color: var(--text);
  fill: var(--text);
  width: 28px;
  height: 28px;
}
.canvas :deep(.vue-flow__controls-button svg) {
  fill: currentColor;
}
.canvas :deep(.vue-flow__controls-button:hover) {
  background: color-mix(in srgb, var(--bg-elevated) 90%, var(--text));
}
.canvas :deep(.vue-flow__minimap) {
  border-radius: 10px;
  overflow: hidden;
  background: var(--bg-elevated);
  box-shadow: var(--shadow-2), var(--hairline);
}
.canvas :deep(.vue-flow__handle) {
  min-width: 0;
  min-height: 0;
}
</style>

<script setup lang="ts">
import '@vue-flow/core/dist/style.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useResizeObserver } from '@vueuse/core'
import { VueFlow, useVueFlow, type Connection, type Edge, type EdgeMouseEvent, type Node, type NodeDragEvent, type NodeMouseEvent } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import StepNode from './StepNode.vue'
import IoNode from './IoNode.vue'
import type { StepStatus } from '@/api/types'
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
  resize: [size: { width: number; height: number }]
}>()

const { fitView, getNodes, getViewport, setViewport, setCenter, dimensions, onNodesInitialized } = useVueFlow(props.id)
// Below MIN_FIT_ZOOM node text is unreadable; such graphs anchor left at LONG_GRAPH_ZOOM and the user pans.
const MIN_FIT_ZOOM = 0.45
const LONG_GRAPH_ZOOM = 0.6
const FIT_PADDING = 0.2
const MINIMAP_MIN_NODES = 13

const root = ref<HTMLElement | null>(null)
const size = ref({ width: 0, height: 0 })
// vue-flow's SVG layers compute NaN patterns/viewBoxes at 0×0 (hidden view, pane mid-resize), so they wait for a real size.
const ready = computed(() => size.value.width > 0 && size.value.height > 0)
const showMinimap = computed(() => props.minimap && ready.value && props.nodes.length >= MINIMAP_MIN_NODES)

async function frame(duration = 280): Promise<void> {
  const nodes = getNodes.value
  if (!ready.value || !nodes.length) return
  await fitView({ padding: FIT_PADDING, duration: 0, maxZoom: 1.05 })
  const v = getViewport()
  if (v.zoom >= MIN_FIT_ZOOM) {
    if (duration) await fitView({ padding: FIT_PADDING, duration, maxZoom: 1.05 })
    return
  }
  const minX = Math.min(...nodes.map((n) => n.position.x))
  const minY = Math.min(...nodes.map((n) => n.position.y))
  const maxY = Math.max(...nodes.map((n) => n.position.y + (n.dimensions.height || 0)))
  const zoom = LONG_GRAPH_ZOOM
  await setViewport({ x: 40 - minX * zoom, y: dimensions.value.height / 2 - ((minY + maxY) / 2) * zoom, zoom }, { duration })
}

let raf = 0
function frameNextPaint(duration = 0): void {
  cancelAnimationFrame(raf)
  raf = requestAnimationFrame(() => void frame(duration))
}
onBeforeUnmount(() => cancelAnimationFrame(raf))

let initialized = false
useResizeObserver(root, (entries) => {
  const r = entries[0]?.contentRect
  if (!r) return
  const width = Math.round(r.width)
  const height = Math.round(r.height)
  if (width === size.value.width && height === size.value.height) return
  size.value = { width, height }
  emit('resize', { width, height })
  if (initialized) frameNextPaint()
})

onNodesInitialized(() => {
  if (initialized) return
  initialized = true
  frameNextPaint()
})
watch(
  () => props.fitKey,
  async () => {
    await nextTick()
    frameNextPaint(280)
  }
)

watch(
  () => props.focusId,
  async (id) => {
    if (!id || !ready.value) return
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

defineExpose({ fit: (animate = true): void => frameNextPaint(animate ? 280 : 0) })

function edgeTone(s: StepStatus): string {
  switch (s) {
    case 'succeeded':
    case 'cached':
      return 'done'
    case 'running':
    case 'waiting_resource':
      return 'flow'
    case 'awaiting_human':
      return 'human'
    case 'failed':
      return 'fail'
    case 'skipped':
    case 'cancelled':
      return 'muted'
    default:
      return ''
  }
}

// A connector says whether work has reached its target, so run edges take the TARGET step's status.
const shownEdges = computed<Edge[]>(() => {
  const status = new Map<string, StepStatus>()
  for (const n of props.nodes) {
    const st = n.type === 'step' ? (n.data as StepNodeData).step?.status : undefined
    if (st) status.set(n.id, st)
  }
  if (!status.size) return props.edges
  return props.edges.map((e) => {
    const st = status.get(e.target)
    if (!st) return e
    return { ...e, class: edgeTone(st), animated: st === 'running' || st === 'waiting_resource' }
  })
})

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
  <div ref="root" class="canvas" :class="{ editable }">
    <VueFlow
      :id="id"
      :nodes="nodes"
      :edges="shownEdges"
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
      <Background v-if="ready" :gap="18" :size="1.4" pattern-color="var(--canvas-dot)" />
      <Controls v-if="ready" :show-interactive="false" position="bottom-right" />
      <MiniMap
        v-if="showMinimap"
        pannable
        zoomable
        position="top-right"
        :width="150"
        :height="96"
        :node-color="miniColor"
        :node-border-radius="6"
        mask-color="color-mix(in srgb, var(--bg-content) 70%, transparent)"
      />
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
.canvas :deep(.vue-flow__edge.human .vue-flow__edge-path) {
  stroke: var(--st-human);
}
.canvas :deep(.vue-flow__edge.fail .vue-flow__edge-path) {
  stroke: var(--st-fail);
}
.canvas :deep(.vue-flow__edge.muted .vue-flow__edge-path) {
  stroke: var(--separator-strong);
  stroke-dasharray: 3 4;
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
  fill: var(--text-2);
  font-size: var(--fs-caption);
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

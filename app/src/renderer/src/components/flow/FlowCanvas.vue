<script setup lang="ts">
import '@vue-flow/core/dist/style.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useMediaQuery, useResizeObserver } from '@vueuse/core'
import { VueFlow, useVueFlow, type Connection, type Edge, type EdgeMouseEvent, type GraphNode, type Node, type NodeDragEvent, type NodeMouseEvent } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { ControlButton, Controls } from '@vue-flow/controls'
import { Maximize } from '@lucide/vue'
import { MiniMap } from '@vue-flow/minimap'
import StepNode from './StepNode.vue'
import IoNode from './IoNode.vue'
import WfNode from './WfNode.vue'
import BandEdge from './BandEdge.vue'
import type { StepStatus } from '@/api/types'
import type { IoNodeData, StepNodeData } from '@/composables/graph'
import type { WfNodeData } from '@/composables/flowGraph'

const props = withDefaults(defineProps<{ id: string; nodes: Node[]; edges: Edge[]; editable?: boolean; fitKey?: string; minimap?: boolean; focusId?: string | null; minFitZoom?: number; fitWidthZoom?: number; scrollPan?: boolean }>(), {
  editable: false,
  fitKey: '',
  minimap: true,
  focusId: null,
  minFitZoom: 0,
  fitWidthZoom: 0,
  scrollPan: false
})
const reducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)')
const emit = defineEmits<{
  connect: [c: Connection]
  nodeClick: [id: string]
  edgeClick: [id: string]
  paneClick: []
  dragStop: [positions: Array<{ id: string; x: number; y: number }>]
  resize: [size: { width: number; height: number }]
}>()

const { getNodes, getViewport, setViewport, setCenter, dimensions, onNodesInitialized } = useVueFlow(props.id)
const MIN_FIT_ZOOM = 0.45
const LONG_GRAPH_ZOOM = 0.6
const FIT_MARGIN = 32
const MINIMAP_MIN_NODES = 13
const MINIMAP_MIN_CANVAS = { width: 640, height: 420 }

const root = ref<HTMLElement | null>(null)
const size = ref({ width: 0, height: 0 })
// vue-flow's SVG layers compute NaN patterns/viewBoxes at 0×0 (hidden view, pane mid-resize), so they wait for a real size.
const ready = computed(() => size.value.width > 0 && size.value.height > 0)
const showMinimap = computed(() => props.minimap && ready.value && props.nodes.length >= MINIMAP_MIN_NODES && size.value.width >= MINIMAP_MIN_CANVAS.width && size.value.height >= MINIMAP_MIN_CANVAS.height)

async function frame(duration = 200): Promise<void> {
  const nodes = getNodes.value
  if (!ready.value || !nodes.length) return
  if (reducedMotion.value) duration = 0
  const floor = props.minFitZoom || MIN_FIT_ZOOM
  const { width, height } = dimensions.value
  const minX = Math.min(...nodes.map((n) => n.position.x))
  const minY = Math.min(...nodes.map((n) => n.position.y))
  const maxX = Math.max(...nodes.map((n) => n.position.x + (n.dimensions.width || 0)))
  const maxY = Math.max(...nodes.map((n) => n.position.y + (n.dimensions.height || 0)))
  const fit = Math.min((width - 2 * FIT_MARGIN) / (maxX - minX), (height - 2 * FIT_MARGIN) / (maxY - minY), 1.05)
  if (fit >= floor) {
    const x = (width - (maxX - minX) * fit) / 2 - minX * fit
    const y = (height - (maxY - minY) * fit) / 2 - minY * fit
    await setViewport({ x, y, zoom: fit }, { duration })
    return
  }
  if (props.minFitZoom) {
    // Tall flows keep readable text (width-fit, 1.0 at most) and scroll: start at the first step or the focused one.
    // fitWidthZoom lets a wide multi-lane graph shrink below the reading floor so no lane is cut off.
    const zoom = Math.max(props.fitWidthZoom || floor, Math.min(1, (width - 2 * FIT_MARGIN) / (maxX - minX)))
    const x = (maxX - minX) * zoom <= width - 2 * FIT_MARGIN ? (width - (maxX - minX) * zoom) / 2 - minX * zoom : FIT_MARGIN - minX * zoom
    const f = props.focusId ? nodes.find((n) => n.id === props.focusId) : undefined
    const top = FIT_MARGIN - minY * zoom
    const y = f ? Math.min(top, height / 2 - (f.position.y + (f.dimensions.height || 0) / 2) * zoom) : top
    await setViewport({ x, y, zoom }, { duration })
    return
  }
  // Below the floor node text is unreadable; keep the floor, anchor left and let the user pan.
  const zoom = LONG_GRAPH_ZOOM
  await setViewport({ x: FIT_MARGIN - minX * zoom, y: height / 2 - ((minY + maxY) / 2) * zoom, zoom }, { duration })
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
    frameNextPaint(200)
  }
)

async function reveal(id: string | null): Promise<void> {
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
  if (!inside) await setCenter(n.position.x + w / 2, n.position.y + h / 2, { zoom: v.zoom, duration: reducedMotion.value ? 0 : 300 })
}
watch(() => props.focusId, reveal)

const ARROW: Record<string, [number, number]> = { ArrowRight: [1, 0], ArrowLeft: [-1, 0], ArrowDown: [0, 1], ArrowUp: [0, -1] }
// Only nodes inside the ±45° cone qualify, so a press never jumps to a diagonal neighbour; the cross-axis penalty keeps it in the same row/column.
function neighbour(from: GraphNode, [dx, dy]: [number, number]): Node | undefined {
  const c = (n: GraphNode): [number, number] => [n.position.x + (n.dimensions.width || 0) / 2, n.position.y + (n.dimensions.height || 0) / 2]
  const [fx, fy] = c(from)
  let best: GraphNode | undefined
  let bestScore = Infinity
  for (const n of getNodes.value) {
    if (n.id === from.id) continue
    const [x, y] = c(n)
    const along = (x - fx) * dx + (y - fy) * dy
    if (along <= 0) continue
    const across = Math.abs((x - fx) * dy) + Math.abs((y - fy) * dx)
    if (across > along) continue
    const score = along + across * 2
    if (score < bestScore) {
      best = n
      bestScore = score
    }
  }
  return best
}

function onKeydown(e: KeyboardEvent): void {
  const el = e.target
  if (!(el instanceof HTMLElement) || e.ctrlKey || e.metaKey || e.altKey) return
  const node = el.closest<HTMLElement>('.vue-flow__node')
  const edge = node ? null : el.closest<HTMLElement>('.vue-flow__edge')
  const id = (node ?? edge)?.dataset.id
  if (!id || (node ?? edge) !== el) return
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    if (node) emit('nodeClick', id)
    else emit('edgeClick', id)
    return
  }
  const dir = ARROW[e.key]
  const from = node && getNodes.value.find((n) => n.id === id)
  if (!dir || !from) return
  e.preventDefault()
  const next = neighbour(from, dir)
  if (!next) return
  root.value?.querySelector<HTMLElement>(`.vue-flow__node[data-id="${CSS.escape(next.id)}"]`)?.focus({ preventScroll: true })
  void reveal(next.id)
}

defineExpose({ fit: (animate = true): void => frameNextPaint(animate ? 200 : 0) })

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

const WF_STEP: Record<string, StepStatus> = { running: 'running', awaiting_human: 'awaiting_human', succeeded: 'succeeded', failed: 'failed', cancelled: 'cancelled', blocked: 'skipped', pending: 'pending' }
const wfStep = (s: string | undefined): StepStatus | undefined => (s ? WF_STEP[s] : undefined)

// A connector says whether work has reached its target, so run edges take the TARGET step's status.
const shownEdges = computed<Edge[]>(() => {
  const status = new Map<string, StepStatus>()
  for (const n of props.nodes) {
    const st = n.type === 'step' ? (n.data as StepNodeData).step?.status : n.type === 'wf' ? wfStep((n.data as WfNodeData).status) : undefined
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
  <div ref="root" class="canvas" :class="{ editable }" @keydown="onKeydown">
    <VueFlow
      :id="id"
      :nodes="nodes"
      :edges="shownEdges"
      :nodes-draggable="editable"
      :nodes-connectable="editable"
      :elements-selectable="false"
      disable-keyboard-a11y
      :delete-key-code="null"
      :pan-on-scroll="scrollPan"
      :zoom-on-scroll="!scrollPan"
      zoom-activation-key-code="Control"
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
      <template #node-wf="p">
        <WfNode :data="p.data as WfNodeData" />
      </template>
      <template #node-io="p">
        <IoNode :data="p.data as IoNodeData" />
      </template>
      <template #edge-band="p">
        <BandEdge v-bind="p" />
      </template>
      <Background v-if="ready" :gap="18" :size="1.4" pattern-color="var(--canvas-dot)" />
      <Controls v-if="ready" :show-interactive="false" position="bottom-right">
        <template #control-fit-view>
          <ControlButton class="vue-flow__controls-fitview" title="Vừa khung" aria-label="Vừa khung" @click="frameNextPaint(200)"><Maximize /></ControlButton>
        </template>
      </Controls>
      <MiniMap
        v-if="showMinimap"
        pannable
        zoomable
        position="bottom-left"
        :width="140"
        :height="90"
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
.canvas :deep(.vue-flow__edge.bind .vue-flow__edge-path),
.canvas :deep(.vue-flow__edge.out .vue-flow__edge-path) {
  stroke-width: 2.2;
}
.canvas :deep(.vue-flow__edge.jump .vue-flow__edge-path) {
  stroke-width: 1.3;
  opacity: 0.5;
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
.canvas :deep(.vue-flow__edge.crowded .vue-flow__edge-textwrapper) {
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--t-fast) var(--ease-out);
}
.canvas :deep(.vue-flow__edge.crowded:hover .vue-flow__edge-textwrapper) {
  opacity: 1;
}
.canvas :deep(.vue-flow__connection-path) {
  stroke: var(--accent);
  stroke-width: 2;
}
.canvas :deep(.vue-flow__node) {
  cursor: pointer;
}
.canvas :deep(.vue-flow__node:focus-visible),
.canvas :deep(.vue-flow__edge:focus-visible) {
  outline: none;
}
.canvas :deep(.vue-flow__node:focus-visible > *) {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.canvas :deep(.vue-flow__edge:focus-visible .vue-flow__edge-path) {
  stroke: var(--accent);
  stroke-width: 2.6;
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
.canvas :deep(.vue-flow__controls-fitview svg) {
  fill: none;
  stroke: currentColor;
  width: 14px;
  height: 14px;
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

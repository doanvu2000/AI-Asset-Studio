<script setup lang="ts">
import { computed } from 'vue'
import type { FlowBatchDetail, RunRow, StepRow, WorkflowRunDetail } from '@/api/types'
import { useNow } from '@/composables/clock'
import { activeSeconds, batchSecondsLeft, estimateEta, etaLabel, flowSecondsLeft, secondsEta, useRecipeMedians } from '@/composables/eta'
import { useFlows } from '@/stores/flows'
import { useRuns } from '@/stores/runs'

const props = defineProps<{ run?: RunRow; steps?: StepRow[]; flow?: WorkflowRunDetail; batch?: FlowBatchDetail }>()
const now = useNow()
const medians = useRecipeMedians()
const runs = useRuns()
const flows = useFlows()

const nodeElapsed = (runId: string | null | undefined): number => {
  const r = runId ? runs.runs.get(runId) : undefined
  return r ? activeSeconds(r, runs.stepsOf(r.id), now.value) : 0
}

const text = computed(() => {
  const { run, flow, batch } = props
  if (run) {
    if (run.status !== 'running') return null
    const eta = estimateEta(medians.value.get(run.recipe_id), activeSeconds(run, props.steps, now.value))
    return eta ? etaLabel(eta) : null
  }
  if (flow) {
    const nodes = flow.def_snapshot.nodes ?? []
    if (flow.status !== 'running' || !nodes.length || flow.children.some((c) => c.status === 'awaiting_human')) return null
    const byNode = new Map(flow.children.map((c) => [c.node_id, c]))
    const eta = secondsEta(flowSecondsLeft(nodes, (id) => byNode.get(id)?.status, medians.value, (id) => nodeElapsed(byNode.get(id)?.run_id)))
    return eta ? etaLabel(eta) : null
  }
  if (batch && batch.status === 'running') {
    const nodes = flows.byId(batch.workflow_id)?.nodes
    const eta = nodes ? secondsEta(batchSecondsLeft(nodes, batch.runs, medians.value)) : null
    return eta ? etaLabel(eta) : null
  }
  return null
})
</script>

<template>
  <span v-if="text" class="eta caption num" title="Ước lượng theo thời gian trung vị của các lượt đã xong">{{ text }}</span>
</template>

<style scoped>
.eta {
  flex: none;
  padding: 1px 8px;
  border-radius: 999px;
  color: var(--text-2);
  background: var(--bg-active);
  white-space: nowrap;
}
</style>

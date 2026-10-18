import type {
  GateAwaitingData,
  LogData,
  PreviewData,
  ProgressData,
  ResourceHealth,
  RunRow,
  RunUpdatedData,
  StepRow,
  StudioEvent,
  VersionRow
} from '@/api/types'
import { useRuns } from './runs'
import { useGates } from './gates'
import { useResources } from './resources'
import { useVersions } from './versions'
import { useRecipes } from './recipes'

// One pass per animation frame: each store mutates in place and triggers its shallowRefs once.
export function applyEvents(events: StudioEvent[]): void {
  const runs = useRuns()
  const gates = useGates()
  const resources = useResources()
  const versions = useVersions()
  const tx = runs.begin()
  for (const e of events) {
    switch (e.type) {
      case 'run.created':
        tx.run(e.data as unknown as RunRow)
        break
      case 'run.updated':
        if (e.run_id) tx.runPatch(e.run_id, e.data as unknown as RunUpdatedData)
        break
      case 'step.updated': {
        const step = e.data as unknown as StepRow
        tx.step(step)
        gates.onStep(step)
        break
      }
      case 'step.progress':
        if (e.step_id) tx.progress(e.step_id, e.data as unknown as ProgressData)
        break
      case 'step.preview':
        if (e.step_id) tx.preview(e.step_id, e.data as unknown as PreviewData)
        break
      case 'step.log':
        if (e.step_id) tx.log(e.step_id, e.data as unknown as LogData, e.ts)
        break
      case 'gate.awaiting':
        if (e.step_id) gates.onAwaiting(e.step_id, e.run_id, e.data as unknown as GateAwaitingData)
        break
      case 'version.updated':
        versions.upsert(e.data as unknown as VersionRow)
        break
      case 'resource.updated':
        resources.upsert(e.data as unknown as ResourceHealth)
        break
    }
  }
  tx.commit()
  gates.commit()
  versions.commit()
  resources.commit()
}

export async function resyncAll(): Promise<void> {
  await Promise.allSettled([
    useResources().load(),
    useRecipes().load(true),
    useRuns().loadList(),
    useRuns().reloadDetails(),
    useGates().load(),
    useVersions().load()
  ])
}

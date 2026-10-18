import { execFile } from 'node:child_process'
import { cpus, freemem, totalmem } from 'node:os'
import type { DeviceInfo, GpuInfo } from '../preload/types'

const MB = 1024 * 1024

function nvidiaSmi(): Promise<GpuInfo[]> {
  const fields = 'name,memory.total,memory.used,utilization.gpu,temperature.gpu,driver_version'
  return new Promise((resolve) => {
    execFile('nvidia-smi', [`--query-gpu=${fields}`, '--format=csv,noheader,nounits'], { timeout: 3000, windowsHide: true }, (err, out) => {
      if (err) return resolve([])
      resolve(
        out
          .trim()
          .split(/\r?\n/)
          .filter(Boolean)
          .map((line) => {
            const [name, total, used, util, temp, driver] = line.split(',').map((s) => s.trim())
            const num = (s?: string): number | null => (s && Number.isFinite(Number(s)) ? Number(s) : null)
            return {
              name: name ?? 'GPU',
              vramTotal: (num(total) ?? 0) * MB,
              vramUsed: (num(used) ?? 0) * MB,
              util: num(util),
              temp: num(temp),
              driver: driver ?? ''
            }
          })
      )
    })
  })
}

export async function deviceInfo(): Promise<DeviceInfo> {
  const list = cpus()
  const gpus = await nvidiaSmi()
  return {
    cpu: (list[0]?.model ?? 'CPU').replace(/\s+/g, ' ').trim(),
    threads: list.length,
    ramTotal: totalmem(),
    ramUsed: totalmem() - freemem(),
    gpus
  }
}

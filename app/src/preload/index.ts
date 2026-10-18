import { contextBridge, ipcRenderer, type IpcRendererEvent } from 'electron'
import type { CoreStatus, StudioBridge } from './types'

const bridge: StudioBridge = {
  getConnection: () => ipcRenderer.invoke('studio:getConnection'),
  mcpInfo: () => ipcRenderer.invoke('studio:mcpInfo'),
  copyText: (text) => ipcRenderer.invoke('studio:copyText', text),
  onCoreStatus(cb) {
    const listener = (_e: IpcRendererEvent, s: CoreStatus): void => cb(s)
    ipcRenderer.on('studio:coreStatus', listener)
    void ipcRenderer.invoke('studio:coreStatus').then(cb)
    return () => ipcRenderer.removeListener('studio:coreStatus', listener)
  },
  log: (level, message) => ipcRenderer.send('studio:log', level, message),
  diagInfo: () => ipcRenderer.invoke('studio:diagInfo'),
  copyDiagnostics: (payload) => ipcRenderer.invoke('studio:copyDiagnostics', payload),
  exportDiagnostics: (payload) => ipcRenderer.invoke('studio:exportDiagnostics', payload),
  openLogsDir: () => ipcRenderer.invoke('studio:openLogsDir'),
  platform: process.platform
}

contextBridge.exposeInMainWorld('studio', bridge)

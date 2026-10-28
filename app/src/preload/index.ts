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
  revealPath: (path) => ipcRenderer.invoke('studio:revealPath', path),
  openSettingsFile: () => ipcRenderer.invoke('studio:openSettingsFile'),
  restartCore: () => ipcRenderer.invoke('studio:restartCore'),
  getTheme: () => ipcRenderer.invoke('studio:getTheme'),
  deviceInfo: () => ipcRenderer.invoke('studio:deviceInfo'),
  setTheme: (mode) => ipcRenderer.invoke('studio:setTheme', mode),
  pickPath: (opts) => ipcRenderer.invoke('studio:pickPath', opts),
  asset: {
    prepare: (hash, name) => ipcRenderer.invoke('studio:asset:prepare', hash, name),
    saveAs: (hash, name) => ipcRenderer.invoke('studio:asset:saveAs', hash, name),
    reveal: (hash, name) => ipcRenderer.invoke('studio:asset:reveal', hash, name),
    copyPath: (hash, name) => ipcRenderer.invoke('studio:asset:copyPath', hash, name),
    startDrag: (hash, name) => ipcRenderer.send('studio:asset:drag', hash, name),
    bundleOptions: (stepId) => ipcRenderer.invoke('studio:asset:bundleOptions', stepId),
    saveBundle: (stepId, preset, name, engine, opts) => ipcRenderer.invoke('studio:asset:saveBundle', stepId, preset, name, engine, opts),
    revealBundle: (stepId, preset, name, engine, opts) =>
      ipcRenderer.invoke('studio:asset:revealBundle', stepId, preset, name, engine, opts)
  },
  setProgress: (value, mode) => ipcRenderer.send('studio:setProgress', value, mode),
  notify: (n) => ipcRenderer.send('studio:notify', n),
  onNavigate(cb) {
    const listener = (_e: IpcRendererEvent, route: string): void => cb(route)
    ipcRenderer.on('studio:navigate', listener)
    return () => ipcRenderer.removeListener('studio:navigate', listener)
  },
  setDirty: (dirty) => ipcRenderer.send('studio:dirty', dirty === true),
  platform: process.platform
}

contextBridge.exposeInMainWorld('studio', bridge)

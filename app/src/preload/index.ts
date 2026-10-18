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
  platform: process.platform
}

contextBridge.exposeInMainWorld('studio', bridge)

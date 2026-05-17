import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  openFile: () => ipcRenderer.invoke('dialog:openFile'),
  openDirectory: () => ipcRenderer.invoke('dialog:openDirectory'),
  readJsonlFile: (path: string) => ipcRenderer.invoke('fs:readJsonlFile', path),
  listJsonlFiles: (dirPath: string) => ipcRenderer.invoke('fs:listJsonlFiles', dirPath),
  watchDirectory: (dirPath: string) => ipcRenderer.invoke('fs:watchDirectory', dirPath),
  onFileChanged: (callback: (data: any) => void) => {
    ipcRenderer.on('fs:fileChanged', (_event, data) => callback(data))
  },
})

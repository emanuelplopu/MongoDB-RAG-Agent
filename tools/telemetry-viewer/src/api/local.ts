declare global {
  interface Window {
    electronAPI: {
      openFile: () => Promise<string[] | null>
      openDirectory: () => Promise<string | null>
      readJsonlFile: (path: string) => Promise<{ success: boolean; records?: any[]; error?: string }>
      listJsonlFiles: (dirPath: string) => Promise<{ success: boolean; files?: any[]; error?: string }>
      watchDirectory: (dirPath: string) => Promise<{ success: boolean }>
      onFileChanged: (callback: (data: any) => void) => void
    }
  }
}

export const localApi = {
  openFile: () => window.electronAPI.openFile(),
  openDirectory: () => window.electronAPI.openDirectory(),
  readJsonlFile: (path: string) => window.electronAPI.readJsonlFile(path),
  listJsonlFiles: (dirPath: string) => window.electronAPI.listJsonlFiles(dirPath),
  watchDirectory: (dirPath: string) => window.electronAPI.watchDirectory(dirPath),
  onFileChanged: (callback: (data: any) => void) => window.electronAPI.onFileChanged(callback),
}

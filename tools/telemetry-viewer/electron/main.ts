import { app, BrowserWindow, ipcMain, dialog } from 'electron'
import * as path from 'path'
import * as fs from 'fs'

let mainWindow: BrowserWindow | null = null

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1000,
    minHeight: 600,
    title: 'RecallHub Telemetry Viewer',
    backgroundColor: '#1a1b26',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL)
    mainWindow.webContents.openDevTools()
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  }
}

// IPC Handlers
ipcMain.handle('dialog:openFile', async () => {
  const result = await dialog.showOpenDialog(mainWindow!, {
    properties: ['openFile', 'multiSelections'],
    filters: [{ name: 'JSONL Files', extensions: ['jsonl'] }],
  })
  if (result.canceled) return null
  return result.filePaths
})

ipcMain.handle('dialog:openDirectory', async () => {
  const result = await dialog.showOpenDialog(mainWindow!, {
    properties: ['openDirectory'],
  })
  if (result.canceled) return null
  return result.filePaths[0]
})

ipcMain.handle('fs:readJsonlFile', async (_event, filePath: string) => {
  try {
    const content = fs.readFileSync(filePath, 'utf-8')
    const records = content
      .split('\n')
      .filter(line => line.trim())
      .map(line => {
        try { return JSON.parse(line) }
        catch { return null }
      })
      .filter(Boolean)
    return { success: true, records, path: filePath }
  } catch (error: any) {
    return { success: false, error: error.message, path: filePath }
  }
})

ipcMain.handle('fs:listJsonlFiles', async (_event, dirPath: string) => {
  try {
    const files = fs.readdirSync(dirPath)
      .filter(f => f.endsWith('.jsonl'))
      .map(f => {
        const fullPath = path.join(dirPath, f)
        const stat = fs.statSync(fullPath)
        return { name: f, path: fullPath, size: stat.size, modified: stat.mtime.toISOString() }
      })
      .sort((a, b) => b.name.localeCompare(a.name))
    return { success: true, files }
  } catch (error: any) {
    return { success: false, error: error.message }
  }
})

ipcMain.handle('fs:watchDirectory', async (_event, dirPath: string) => {
  // Set up a watcher and send events to renderer
  const watcher = fs.watch(dirPath, (eventType, filename) => {
    if (filename?.endsWith('.jsonl') && mainWindow) {
      mainWindow.webContents.send('fs:fileChanged', { eventType, filename, dirPath })
    }
  })
  return { success: true }
})

app.whenReady().then(createWindow)
app.on('window-all-closed', () => app.quit())

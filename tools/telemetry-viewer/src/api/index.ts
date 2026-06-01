import { createContext, useContext } from 'react'
import { TelemetryRecord } from '../types/telemetry'

export interface DataSourceState {
  records: TelemetryRecord[]
  loading: boolean
  error: string | null
  source: 'local' | 'remote' | 'none'
  connectionInfo?: { url: string; token: string; connected: boolean }
  filesLoaded: string[]
}

export interface DataSourceActions {
  // Local
  openFile: () => Promise<void>
  openDirectory: () => Promise<void>
  loadFromPath: (path: string) => Promise<void>

  // Remote
  connect: (url: string, token: string) => Promise<boolean>
  fetchByDate: (date: string, source?: 'protected' | 'raw') => Promise<void>
  fetchAll: (range?: string) => Promise<void>
  searchRecords: (filters: SearchFilters) => Promise<void>

  // Common
  clearRecords: () => void
  getRecordById: (id: string) => TelemetryRecord | undefined
  getSessionIds: () => string[]
  filterBySession: (sessionId: string) => TelemetryRecord[]
  filterByModel: (model: string) => TelemetryRecord[]
  filterByDateRange: (from: string, to: string) => TelemetryRecord[]
}

export interface SearchFilters {
  session_id?: string
  model?: string
  date_from?: string
  date_to?: string
  min_latency?: number
  query_text?: string
  limit?: number
}

export const DataSourceContext = createContext<{
  state: DataSourceState
  actions: DataSourceActions
} | null>(null)

export function useDataSource() {
  const ctx = useContext(DataSourceContext)
  if (!ctx) throw new Error('useDataSource must be inside DataSourceProvider')
  return ctx
}

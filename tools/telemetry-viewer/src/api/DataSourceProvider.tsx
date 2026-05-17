import React, { useState, useCallback, useMemo } from 'react'
import { DataSourceContext, DataSourceState, DataSourceActions, SearchFilters } from './index'
import { TelemetryRecord } from '../types/telemetry'
import { localApi } from './local'
import { remoteApi } from './remote'

export function DataSourceProvider({ children }: { children: React.ReactNode }) {
  const [records, setRecords] = useState<TelemetryRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [source, setSource] = useState<'local' | 'remote' | 'none'>('none')
  const [filesLoaded, setFilesLoaded] = useState<string[]>([])
  const [connectionInfo, setConnectionInfo] = useState<
    { url: string; token: string; connected: boolean } | undefined
  >()

  const openFile = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const paths = await localApi.openFile()
      if (!paths) { setLoading(false); return }

      const allRecords: TelemetryRecord[] = []
      for (const path of paths) {
        const result = await localApi.readJsonlFile(path)
        if (result.success && result.records) {
          allRecords.push(...result.records)
        }
      }
      setRecords(prev => [...prev, ...allRecords])
      setFilesLoaded(prev => [...prev, ...paths])
      setSource('local')
    } catch (e: any) {
      setError(e.message || 'Failed to open file')
    } finally {
      setLoading(false)
    }
  }, [])

  const openDirectory = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const dirPath = await localApi.openDirectory()
      if (!dirPath) { setLoading(false); return }

      const listResult = await localApi.listJsonlFiles(dirPath)
      if (!listResult.success || !listResult.files) {
        setError(listResult.error || 'Failed to list files')
        setLoading(false)
        return
      }

      const allRecords: TelemetryRecord[] = []
      for (const file of listResult.files) {
        const result = await localApi.readJsonlFile(file.path)
        if (result.success && result.records) {
          allRecords.push(...result.records)
        }
      }
      setRecords(prev => [...prev, ...allRecords])
      setFilesLoaded(prev => [...prev, ...(listResult.files ?? []).map((f: any) => f.path)])
      setSource('local')
    } catch (e: any) {
      setError(e.message || 'Failed to open directory')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadFromPath = useCallback(async (path: string) => {
    setLoading(true)
    setError(null)
    try {
      const result = await localApi.readJsonlFile(path)
      if (result.success && result.records) {
        const loaded = result.records
        setRecords(prev => [...prev, ...loaded])
        setFilesLoaded(prev => [...prev, path])
        setSource('local')
      } else {
        setError(result.error || 'Failed to read file')
      }
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const connect = useCallback(async (url: string, token: string) => {
    setLoading(true)
    setError(null)
    try {
      await remoteApi.connect(url, token)
      setConnectionInfo({ url, token, connected: true })
      setSource('remote')
      setLoading(false)
      return true
    } catch (e: any) {
      setError(e.message || 'Connection failed')
      setConnectionInfo({ url, token, connected: false })
      setLoading(false)
      return false
    }
  }, [])

  const fetchByDate = useCallback(
    async (date: string, dataSource: 'protected' | 'raw' = 'protected') => {
      if (!connectionInfo?.connected) { setError('Not connected'); return }
      setLoading(true)
      setError(null)
      try {
        let offset = 0
        const limit = 100
        let hasMore = true
        const fetched: TelemetryRecord[] = []

        while (hasMore) {
          const result = await remoteApi.fetchRecords(
            connectionInfo.url,
            connectionInfo.token,
            { date, source: dataSource, offset, limit },
          )
          fetched.push(...result.records)
          hasMore = result.has_more
          offset += limit
        }

        setRecords(prev => [...prev, ...fetched])
        setFilesLoaded(prev => [...prev, `remote:${date}:${dataSource}`])
      } catch (e: any) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    },
    [connectionInfo],
  )

  const fetchAll = useCallback(
    async (range = '7d') => {
      if (!connectionInfo?.connected) { setError('Not connected'); return }
      setLoading(true)
      setError(null)
      try {
        const filesResult = await remoteApi.fetchFiles(
          connectionInfo.url,
          connectionInfo.token,
        )
        const now = new Date()
        const days = range === '30d' ? 30 : range === 'all' ? 9999 : 7
        const cutoff = new Date(now.getTime() - days * 24 * 60 * 60 * 1000)

        const allRecords: TelemetryRecord[] = []
        for (const file of filesResult.files) {
          const fileDate = new Date(file.date)
          if (fileDate >= cutoff) {
            const result = await remoteApi.fetchRecords(
              connectionInfo.url,
              connectionInfo.token,
              { date: file.date, source: 'protected', offset: 0, limit: 1000 },
            )
            allRecords.push(...result.records)
          }
        }
        setRecords(prev => [...prev, ...allRecords])
      } catch (e: any) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    },
    [connectionInfo],
  )

  const searchRecords = useCallback(
    async (filters: SearchFilters) => {
      if (!connectionInfo?.connected) { setError('Not connected'); return }
      setLoading(true)
      setError(null)
      try {
        const result = await remoteApi.searchRecords(
          connectionInfo.url,
          connectionInfo.token,
          filters,
        )
        setRecords(result.records || [])
      } catch (e: any) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    },
    [connectionInfo],
  )

  const clearRecords = useCallback(() => {
    setRecords([])
    setFilesLoaded([])
    setError(null)
  }, [])

  const getRecordById = useCallback(
    (id: string) => records.find(r => r.record_id === id),
    [records],
  )

  const getSessionIds = useCallback(
    () => [...new Set(records.map(r => r.session_id).filter(Boolean))],
    [records],
  )

  const filterBySession = useCallback(
    (sessionId: string) => records.filter(r => r.session_id === sessionId),
    [records],
  )

  const filterByModel = useCallback(
    (model: string) =>
      records.filter(r => r.orchestrator_model === model || r.worker_model === model),
    [records],
  )

  const filterByDateRange = useCallback(
    (from: string, to: string) =>
      records.filter(r => r.timestamp >= from && r.timestamp <= to),
    [records],
  )

  const state: DataSourceState = useMemo(
    () => ({
      records,
      loading,
      error,
      source,
      filesLoaded,
      connectionInfo: connectionInfo
        ? { url: connectionInfo.url, connected: connectionInfo.connected }
        : undefined,
    }),
    [records, loading, error, source, filesLoaded, connectionInfo],
  )

  const actions: DataSourceActions = useMemo(
    () => ({
      openFile,
      openDirectory,
      loadFromPath,
      connect,
      fetchByDate,
      fetchAll,
      searchRecords,
      clearRecords,
      getRecordById,
      getSessionIds,
      filterBySession,
      filterByModel,
      filterByDateRange,
    }),
    [
      openFile, openDirectory, loadFromPath,
      connect, fetchByDate, fetchAll, searchRecords,
      clearRecords, getRecordById, getSessionIds,
      filterBySession, filterByModel, filterByDateRange,
    ],
  )

  return (
    <DataSourceContext.Provider value={{ state, actions }}>
      {children}
    </DataSourceContext.Provider>
  )
}

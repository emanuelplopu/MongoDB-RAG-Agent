import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import DbStatsSection, { minCoverage } from './DbStatsSection'
import type { DbDatabaseStats, DbSample, DbSeriesPoint, DbStatsResponse } from '../../api/client'

const collectMock = vi.fn()

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: unknown) => (typeof opts === 'string' ? opts : key),
    i18n: { language: 'en' },
  }),
}))

vi.mock('../../api/client', () => {
  class ApiError extends Error {
    status: number
    constructor(message: string, status: number) {
      super(message)
      this.status = status
    }
  }
  return {
    ApiError,
    telemetryApi: { collectDbStats: (...a: unknown[]) => collectMock(...a) },
  }
})

const GB = 1024 ** 3
const START = '2026-10-18T10:00:00+00:00'
const END = '2026-10-18T10:30:00+00:00'

export function dbPoint(t: string, chunks: number, overrides: Partial<DbSeriesPoint> = {}): DbSeriesPoint {
  return {
    t, connections_current: 71, connections_total_created: 28000, connections_created_per_min: 30.4,
    wt_cache_used_bytes: 4 * GB, wt_cache_max_bytes: 62 * GB, ops_insert: 1000, ops_query: 500, inserts_per_min: 12,
    oplog_bytes: 21 * GB, oplog_max_bytes: 50 * GB, oplog_window_s: 334 * 3600, fs_used_bytes: 600 * GB, fs_total_bytes: 3600 * GB,
    size_on_disk_bytes: 2.32 * GB, data_bytes: 3.3 * GB, storage_bytes: 2.3 * GB, index_bytes: 8 * 1024 ** 2, chunks, documents: 47823,
    databases: [{ name: 'rag_a', documents: 47823, chunks, storage_bytes: 2.3 * GB, data_bytes: 3.3 * GB, index_bytes: 8 * 1024 ** 2,
      embedding_coverage_pct: 100, search_lag: 988 }],
    ...overrides,
  }
}

export const database: DbDatabaseStats = {
  name: 'rag_a', profile_key: 'a', data_bytes: 3.3 * GB, storage_bytes: 2.3 * GB, index_bytes: 8 * 1024 ** 2, size_on_disk_bytes: 2.32 * GB,
  collections: 20, objects: 347851,
  documents: { count: 47823, data_bytes: 217e6, storage_bytes: 90e6, index_bytes: 1e6, avg_bytes: 4759 },
  chunks: { count: 299988, data_bytes: 3.1 * GB, storage_bytes: 2.2 * GB, index_bytes: 8e6, avg_bytes: 11118 },
  sample: { size: 1000, with_embedding: 1000, embedding_coverage_pct: 100, embedding_dims: { '768': 1000 },
    embedding_models: { 'embeddinggemma-2': 1000 }, chunk_methods: { hybrid: 900, simple_fallback: 100 }, with_context_pct: 78, token_p50: 324, token_p95: 514 },
  document_sample: { size: 500, zero_chunks: 0, zero_chunks_pct: 0 },
  search_indexes: [
    { name: 'vector_index', type: 'vectorSearch', status: 'READY', queryable: true, dimensions: 768, similarity: 'cosine', quantization: null },
    { name: 'text_index', type: 'search', status: 'BUILDING', queryable: false, dimensions: null, similarity: null, quantization: null },
  ],
  search_lag: { index: 'text_index', indexed_count: 299000, collection_count: 299988, lag: 988 },
  jobs_by_status: { running: 1, completed: 2 },
  backup: { count: 2, newest_at: '2026-10-15T22:42:38Z', newest_status: 'completed', newest_type: 'post_ingestion', newest_size_bytes: 0 },
  failed_documents: null,
  file_registry: null,
}

export const sample: DbSample = {
  ts: END, collector: 'quellex:api', forced: false, duration_ms: 1200,
  server: { version: '8.0.16', uptime_s: 58000, connections: { current: 71, active: 21, available: 32000, total_created: 28916 },
    wt_cache: { used_bytes: 4 * GB, max_bytes: 62 * GB, dirty_bytes: 6e6 }, opcounters: { insert: 1000 }, mem_resident_bytes: 5 * GB },
  oplog: { data_bytes: 32 * GB, storage_bytes: 21 * GB, max_bytes: 50 * GB, window_s: 334 * 3600, first_at: START, last_at: END },
  fs: { used_bytes: 600 * GB, total_bytes: 3600 * GB, pct: 16.4 },
  telemetry: { data_bytes: 1, storage_bytes: 1, index_bytes: 1, size_on_disk_bytes: 1 },
  totals: { databases: 1, documents: 47823, chunks: 299988, data_bytes: 3.3 * GB, storage_bytes: 2.3 * GB, index_bytes: 8 * 1024 ** 2, size_on_disk_bytes: 2.32 * GB },
  databases: [database],
  errors: {},
}

export const dbStats: DbStatsResponse = {
  start: START, end: END, bucket_seconds: 1800, interval_seconds: 1800, samples: 2,
  points: [dbPoint(START, 227041), dbPoint(END, 299988)],
  latest: sample, next_due_at: '2026-10-18T11:00:00+00:00', lease_holder: 'quellex:api',
}

const fmt = { iso: (iso: string) => `iso(${iso})`, axis: (ms: number) => String(ms) }

describe('DbStatsSection', () => {
  beforeEach(() => {
    collectMock.mockReset()
  })

  it('renders cards with values, deltas, status and the per-database table', () => {
    render(<DbStatsSection data={dbStats} error={null} hours={24} onRefresh={() => undefined} formatIso={fmt.iso} formatAxis={fmt.axis} />)

    expect(screen.getByText('telemetry.db.title')).toBeInTheDocument()
    expect(screen.getByText(/telemetry\.db\.lastSample · telemetry\.db\.next/)).toBeInTheDocument()
    expect(screen.getAllByText('299,988').length).toBeGreaterThan(0) // chunks card + table
    expect(screen.getByText('+72,947 · telemetry.db.lastHours')).toBeInTheDocument() // delta over the range
    expect(screen.getAllByText('2.3 GB').length).toBeGreaterThan(0) // storage card + table
    expect(screen.getByText('21.0 GB')).toBeInTheDocument() // oplog card
    expect(screen.getAllByText('71').length).toBeGreaterThan(0) // connections card (+ chart end label)
    expect(screen.getAllByText('100%').length).toBeGreaterThan(0) // coverage card (+ table, chart ticks)
    expect(screen.getAllByText('768-d').length).toBeGreaterThan(0) // coverage sub-line + table
    const building = screen.getByText('text_index: BUILDING')
    expect(building.className).toContain('text-amber-700')
    expect(screen.getByText('vector_index: READY').className).not.toContain('text-amber-700')
    const backup = screen.getByText('telemetry.db.ago')
    expect(backup.className).toContain('text-amber-700') // empty backup
    for (const title of ['telemetry.db.chart.counts', 'telemetry.db.chart.storage', 'telemetry.db.chart.connections', 'telemetry.db.chart.coverage', 'telemetry.db.chart.lag']) {
      expect(screen.getByText(title)).toBeInTheDocument()
    }
    const table = screen.getByRole('table', { name: 'telemetry.db.table' })
    expect(table).toHaveTextContent('rag_a')
    expect(table).toHaveTextContent('47,823')
    expect(table).toHaveTextContent('running 1, completed 2')
  })

  it('hides deltas with a single point and the lag chart without lag', () => {
    const single = { ...dbStats, points: [dbPoint(END, 299988, { databases: [{ ...dbPoint(END, 1).databases[0], search_lag: null }] })] }
    render(<DbStatsSection data={single} error={null} hours={6} onRefresh={() => undefined} formatIso={fmt.iso} formatAxis={fmt.axis} />)
    expect(screen.queryByText(/telemetry\.db\.lastHours/)).not.toBeInTheDocument()
    expect(screen.queryByText('telemetry.db.chart.lag')).not.toBeInTheDocument()
  })

  it('shows the empty state and lets the user sample now', async () => {
    const onRefresh = vi.fn()
    collectMock.mockResolvedValue(sample)
    render(<DbStatsSection data={{ ...dbStats, points: [], latest: null }} error={null} hours={24} onRefresh={onRefresh} formatIso={fmt.iso} formatAxis={fmt.axis} />)

    expect(screen.getByText('telemetry.db.noSample')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'telemetry.db.collectNow' }))
    await waitFor(() => expect(collectMock).toHaveBeenCalled())
    await waitFor(() => expect(onRefresh).toHaveBeenCalled())
  })

  it('explains a missing collector (503) and other failures', async () => {
    const { ApiError } = await import('../../api/client')
    collectMock.mockRejectedValue(new (ApiError as unknown as new (m: string, s: number) => Error)('off', 503))
    render(<DbStatsSection data={dbStats} error={null} hours={24} onRefresh={() => undefined} formatIso={fmt.iso} formatAxis={fmt.axis} />)

    fireEvent.click(screen.getByRole('button', { name: 'telemetry.db.collectNow' }))
    expect(await screen.findByText('telemetry.db.collectorOff')).toBeInTheDocument()

    collectMock.mockRejectedValue(new Error('boom'))
    fireEvent.click(screen.getByRole('button', { name: 'telemetry.db.collectNow' }))
    expect(await screen.findByText('telemetry.db.collectFailed')).toBeInTheDocument()
  })

  it('shows a load error but keeps the sample button', () => {
    render(<DbStatsSection data={null} error="nope" hours={24} onRefresh={() => undefined} formatIso={fmt.iso} formatAxis={fmt.axis} />)
    expect(screen.getByText('nope')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'telemetry.db.collectNow' })).toBeInTheDocument()
  })
})

describe('minCoverage', () => {
  it('takes the lowest coverage over databases with a sample', () => {
    const b = { ...database, name: 'b', sample: { ...database.sample!, embedding_coverage_pct: 96.5 } }
    const c = { ...database, name: 'c', sample: null }
    expect(minCoverage([database, b, c])).toBe(96.5)
    expect(minCoverage([c])).toBeNull()
  })
})

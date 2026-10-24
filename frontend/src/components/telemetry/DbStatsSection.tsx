import { ReactNode, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowPathIcon } from '@heroicons/react/24/outline'
import { ApiError, telemetryApi, DbDatabaseStats, DbSeriesPoint, DbStatsResponse } from '../../api/client'
import TimeSeriesChart, { ChartSeries, SeriesSlot } from './TimeSeriesChart'
import { formatBytesShort, formatCount, formatSeconds, formatSignedBytes, formatSignedCount } from './format'

const GB = 1024 ** 3
const BACKUP_STALE_SECONDS = 7 * 86400

type Tone = 'default' | 'warn'

interface StatCardProps {
  label: string
  value: ReactNode
  /** Change over the range ("+72,947 · last 24 h"); hidden when null. */
  delta?: string | null
  /** Muted context line. */
  sub?: string | null
  tone?: Tone
}

/** One figure with its change over the range and a context line. Status colour only for `tone`. */
function StatCard({ label, value, delta, sub, tone = 'default' }: StatCardProps) {
  const valueColor = tone === 'warn' ? 'text-amber-700 dark:text-amber-300' : 'text-gray-900 dark:text-gray-100'
  return (
    <div className="rounded-xl border border-gray-200 p-3 dark:border-gray-700">
      <div className="text-xs font-medium text-gray-500 dark:text-gray-400">{label}</div>
      <div className={`mt-1 text-xl font-semibold ${valueColor}`} style={{ fontVariantNumeric: 'tabular-nums' }}>{value}</div>
      {delta && <div className="mt-0.5 text-xs text-gray-700 dark:text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>{delta}</div>}
      {sub && <div className="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400" title={sub}>{sub}</div>}
    </div>
  )
}

/** Lowest embedding coverage over the databases that have a chunk sample. */
export function minCoverage(databases: DbDatabaseStats[]): number | null {
  const values = databases
    .map(d => d.sample)
    .filter((s): s is NonNullable<DbDatabaseStats['sample']> => s !== null && s.size > 0 && s.embedding_coverage_pct !== null)
    .map(s => s.embedding_coverage_pct as number)
  return values.length ? Math.min(...values) : null
}

/** Lowest coverage of one series point (over its databases). */
function pointCoverage(p: DbSeriesPoint): number | null {
  const values = p.databases.map(d => d.embedding_coverage_pct).filter((v): v is number => v !== null)
  return values.length ? Math.min(...values) : null
}

/** Summed search lag of one series point, null when no database reports one. */
function pointLag(p: DbSeriesPoint): number | null {
  const values = p.databases.map(d => d.search_lag).filter((v): v is number => v !== null)
  return values.length ? values.reduce((a, b) => a + b, 0) : null
}

function indexHealthy(status: string | null, queryable: boolean | null): boolean {
  return status === 'READY' && queryable !== false
}

export interface DbStatsSectionProps {
  data: DbStatsResponse | null
  /** Load error for the whole section (the rest of the page still shows). */
  error: string | null
  /** Range in hours (for the delta label). */
  hours: number
  /** Re-fetch after a forced sample. */
  onRefresh: () => void | Promise<void>
  formatIso: (iso: string) => string
  formatAxis: (ms: number, span: number) => string
}

/**
 * MongoDB statistics from `db_samples`: stat cards with their change over the range, charts on
 * the samples' own time grid (sampling runs every 30 min, not every 5 s like the host agent),
 * and a per-database table from the newest full sample. The "sample now" button asks the backend
 * for a sample outside the schedule.
 */
export default function DbStatsSection({ data, error, hours, onRefresh, formatIso, formatAxis }: DbStatsSectionProps) {
  const { t, i18n } = useTranslation()
  const locale = i18n?.language || undefined
  const [collecting, setCollecting] = useState(false)
  const [collectError, setCollectError] = useState<string | null>(null)
  const [hover, setHover] = useState<number | null>(null)

  const latest = data?.latest ?? null
  const points = useMemo(() => data?.points ?? [], [data])
  const first = points[0]
  const last = points[points.length - 1]
  const rangeLabel = hours >= 48 ? t('telemetry.db.lastDays', { count: Math.round(hours / 24) }) : t('telemetry.db.lastHours', { count: hours })

  const countDelta = (pick: (p: DbSeriesPoint) => number | null): string | null => {
    if (points.length < 2) return null
    const a = pick(first)
    const b = pick(last)
    return a === null || b === null ? null : `${formatSignedCount(b - a, locale)} · ${rangeLabel}`
  }
  const bytesDelta = (pick: (p: DbSeriesPoint) => number | null): string | null => {
    if (points.length < 2) return null
    const a = pick(first)
    const b = pick(last)
    return a === null || b === null ? null : `${formatSignedBytes(b - a)} · ${rangeLabel}`
  }

  const collect = async () => {
    setCollecting(true)
    setCollectError(null)
    try {
      await telemetryApi.collectDbStats()
      await onRefresh()
    } catch (err) {
      setCollectError(err instanceof ApiError && err.status === 503 ? t('telemetry.db.collectorOff') : t('telemetry.db.collectFailed'))
    } finally {
      setCollecting(false)
    }
  }

  const charts = useMemo(() => {
    const times = points.map(p => Date.parse(p.t))
    const series = (key: string, label: string, slot: SeriesSlot, pick: (p: DbSeriesPoint) => number | null): ChartSeries => ({
      key, label, slot, values: points.map(pick),
    })
    const gb = (v: number | null) => (v === null ? null : v / GB)
    const hasLag = points.some(p => pointLag(p) !== null)
    const list = [
      { key: 'counts', title: t('telemetry.db.chart.counts'), unit: t('telemetry.db.units.count'), integer: true, yMax: undefined as number | undefined,
        series: [series('chunks', t('telemetry.db.series.chunks'), 1, p => p.chunks), series('documents', t('telemetry.db.series.documents'), 2, p => p.documents)] },
      { key: 'storage', title: t('telemetry.db.chart.storage'), unit: 'GB', integer: false, yMax: undefined as number | undefined,
        series: [
          series('storage', t('telemetry.db.series.storage'), 1, p => gb(p.storage_bytes)),
          series('index', t('telemetry.db.series.index'), 2, p => gb(p.index_bytes)),
          series('oplog', t('telemetry.db.series.oplog'), 3, p => gb(p.oplog_bytes)),
          series('data', t('telemetry.db.series.data'), 4, p => gb(p.data_bytes)),
        ] },
      { key: 'connections', title: t('telemetry.db.chart.connections'), unit: t('telemetry.db.units.count'), integer: true, yMax: undefined as number | undefined,
        series: [series('connections', t('telemetry.db.series.connections'), 1, p => p.connections_current)] },
      { key: 'churn', title: t('telemetry.db.chart.churn'), unit: t('telemetry.db.units.perMinute'), integer: false, yMax: undefined as number | undefined,
        series: [series('churn', t('telemetry.db.series.churn'), 2, p => p.connections_created_per_min)] },
      { key: 'coverage', title: t('telemetry.db.chart.coverage'), unit: '%', integer: false, yMax: 100 as number | undefined,
        series: [series('coverage', t('telemetry.db.series.coverage'), 1, pointCoverage)] },
    ]
    if (hasLag) {
      list.push({ key: 'lag', title: t('telemetry.db.chart.lag'), unit: t('telemetry.db.units.count'), integer: true, yMax: undefined,
        series: [series('lag', t('telemetry.db.series.lag'), 3, pointLag)] })
    }
    return { times, list }
  }, [points, t])

  const unitFormat = (unit: string) => (v: number) =>
    unit === '%' ? `${Math.round(v)}%` : unit === 'GB' ? `${v >= 10 ? Math.round(v) : v.toFixed(1)} GB` : v >= 10 ? formatCount(v, locale) : v.toFixed(1)

  // Cards from the newest sample.
  const totals = latest?.totals
  const oplog = latest?.oplog ?? null
  const connections = latest?.server?.connections ?? null
  const coverage = latest ? minCoverage(latest.databases) : null
  const dims = latest ? Array.from(new Set(latest.databases.flatMap(d => Object.keys(d.sample?.embedding_dims ?? {})))) : []
  const indexes = latest ? latest.databases.flatMap(d => d.search_indexes.map(i => ({ db: d.name, ...i }))) : []
  const lagTotal = latest ? latest.databases.reduce((sum, d) => sum + (d.search_lag?.lag ?? 0), 0) : 0
  const anyLag = latest ? latest.databases.some(d => d.search_lag !== null) : false
  const backups = latest ? latest.databases.filter(d => (d.documents?.count ?? 0) > 0).map(d => ({ db: d.name, backup: d.backup })) : []
  const newestBackup = backups
    .map(b => b.backup)
    .filter((b): b is NonNullable<typeof b> => b !== null && b.newest_at !== null)
    .sort((a, b) => Date.parse(b.newest_at as string) - Date.parse(a.newest_at as string))[0]
  const backupAge = newestBackup?.newest_at && latest ? (Date.parse(latest.ts) - Date.parse(newestBackup.newest_at)) / 1000 : null
  const backupStale = backups.length > 0 && (!newestBackup || !newestBackup.newest_size_bytes || (backupAge ?? 0) > BACKUP_STALE_SECONDS)

  const collectButton = (
    <button type="button" onClick={collect} disabled={collecting}
      className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-700">
      <ArrowPathIcon className={`h-4 w-4 ${collecting ? 'animate-spin' : ''}`} />
      {collecting ? t('telemetry.db.collecting') : t('telemetry.db.collectNow')}
    </button>
  )

  return (
    <section className="space-y-4 rounded-2xl bg-surface p-4 shadow-elevation-1 dark:bg-gray-800" aria-label={t('telemetry.db.title')}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">{t('telemetry.db.title')}</h2>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {latest
              ? `${t('telemetry.db.lastSample', { time: formatIso(latest.ts), by: latest.collector ?? '—' })}${
                  data?.next_due_at ? ` · ${t('telemetry.db.next', { time: formatIso(data.next_due_at) })}` : ''}`
              : t('telemetry.db.subtitle')}
          </p>
        </div>
        {collectButton}
      </div>

      {(error || collectError) && (
        <div className="rounded-xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-300">{collectError ?? error}</div>
      )}

      {!latest && !error && (
        <p className="text-sm text-gray-500 dark:text-gray-400">{t('telemetry.db.noSample')}</p>
      )}

      {latest && totals && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard
              label={t('telemetry.db.cards.chunks')}
              value={formatCount(totals.chunks, locale)}
              delta={countDelta(p => p.chunks)}
              sub={totals.documents > 0 ? t('telemetry.db.perDocument', { value: (totals.chunks / totals.documents).toFixed(1) }) : null}
            />
            <StatCard
              label={t('telemetry.db.cards.documents')}
              value={formatCount(totals.documents, locale)}
              delta={countDelta(p => p.documents)}
              sub={t('telemetry.db.databases', { count: totals.databases })}
            />
            <StatCard
              label={t('telemetry.db.cards.storage')}
              value={formatBytesShort(totals.storage_bytes)}
              delta={bytesDelta(p => p.storage_bytes)}
              sub={t('telemetry.db.dataIndex', { data: formatBytesShort(totals.data_bytes), index: formatBytesShort(totals.index_bytes) })}
            />
            <StatCard
              label={t('telemetry.db.cards.oplog')}
              value={formatBytesShort(oplog?.storage_bytes)}
              delta={bytesDelta(p => p.oplog_bytes)}
              sub={oplog ? t('telemetry.db.oplogSub', { max: formatBytesShort(oplog.max_bytes), hours: Math.round((oplog.window_s ?? 0) / 3600) }) : null}
            />
            <StatCard
              label={t('telemetry.db.cards.connections')}
              value={formatCount(connections?.current, locale)}
              sub={last?.connections_created_per_min != null ? t('telemetry.db.newPerMin', { value: last.connections_created_per_min.toFixed(1) }) : null}
            />
            <StatCard
              label={t('telemetry.db.cards.coverage')}
              value={coverage === null ? '—' : `${coverage % 1 === 0 ? coverage.toFixed(0) : coverage.toFixed(1)}%`}
              tone={coverage !== null && coverage < 100 ? 'warn' : 'default'}
              sub={dims.length ? dims.map(d => (/^\d+$/.test(d) ? `${d}-d` : d)).join(', ') : null}
            />
            <StatCard
              label={t('telemetry.db.cards.indexes')}
              value={
                indexes.length === 0 ? '—' : (
                  <span className="flex flex-wrap gap-1">
                    {indexes.map(i => (
                      <span key={`${i.db}:${i.name}`}
                        className={`rounded-md px-1.5 py-0.5 text-xs font-medium ${indexHealthy(i.status, i.queryable)
                          ? 'bg-gray-100 text-gray-700 dark:bg-gray-700/60 dark:text-gray-200'
                          : 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'}`}
                        title={`${i.db} · ${i.type ?? ''} · ${i.status ?? '?'}${i.queryable === false ? ' · not queryable' : ''}`}>
                        {i.name}: {i.status ?? '?'}
                      </span>
                    ))}
                  </span>
                )
              }
              tone={indexes.some(i => !indexHealthy(i.status, i.queryable)) ? 'warn' : 'default'}
              sub={anyLag ? t('telemetry.db.lag', { count: formatCount(lagTotal, locale) }) : null}
            />
            <StatCard
              label={t('telemetry.db.cards.backup')}
              value={newestBackup?.newest_at ? t('telemetry.db.ago', { age: formatSeconds(backupAge) }) : t('telemetry.db.noBackup')}
              tone={backupStale ? 'warn' : 'default'}
              sub={newestBackup?.newest_at
                ? t('telemetry.db.backupSub', { size: formatBytesShort(newestBackup.newest_size_bytes ?? 0), time: formatIso(newestBackup.newest_at) })
                : null}
            />
          </div>

          {points.length > 0 && (
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              {charts.list.map(chart => (
                <TimeSeriesChart
                  key={chart.key}
                  title={chart.title}
                  unit={chart.unit}
                  times={charts.times}
                  series={chart.series}
                  yMax={chart.yMax}
                  integer={chart.integer}
                  hoverIndex={hover}
                  onHoverIndex={setHover}
                  format={chart.integer ? (v: number) => formatCount(v, locale) : unitFormat(chart.unit)}
                  emptyLabel={t('telemetry.noData')}
                  formatTime={formatAxis}
                />
              ))}
            </div>
          )}

          <div className="overflow-x-auto">
            <table className="min-w-full text-sm" aria-label={t('telemetry.db.table')}>
              <thead>
                <tr>
                  {['database', 'profile', 'documents', 'chunks', 'storage', 'index', 'embedded', 'dims', 'indexes', 'lag', 'backup', 'jobs'].map(k => (
                    <th key={k} className="whitespace-nowrap px-2 py-1.5 text-left text-xs font-medium text-gray-500 dark:text-gray-400">{t(`telemetry.db.col.${k}`)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {latest.databases.map(d => (
                  <tr key={d.name} className="border-t border-gray-100 dark:border-gray-700" style={{ fontVariantNumeric: 'tabular-nums' }}>
                    <td className="whitespace-nowrap px-2 py-1.5 font-mono text-xs">{d.name}</td>
                    <td className="px-2 py-1.5">{d.profile_key ?? '—'}</td>
                    <td className="px-2 py-1.5 text-right">{formatCount(d.documents?.count, locale)}</td>
                    <td className="px-2 py-1.5 text-right">{formatCount(d.chunks?.count, locale)}</td>
                    <td className="px-2 py-1.5 text-right">{formatBytesShort(d.storage_bytes)}</td>
                    <td className="px-2 py-1.5 text-right">{formatBytesShort(d.index_bytes)}</td>
                    <td className="px-2 py-1.5 text-right">{d.sample?.embedding_coverage_pct == null ? '—' : `${d.sample.embedding_coverage_pct}%`}</td>
                    <td className="px-2 py-1.5">{Object.keys(d.sample?.embedding_dims ?? {}).map(k => (/^\d+$/.test(k) ? `${k}-d` : k)).join(', ') || '—'}</td>
                    <td className="px-2 py-1.5">
                      {d.search_indexes.length === 0 ? '—' : d.search_indexes.map(i => `${i.name}: ${i.status ?? '?'}`).join(', ')}
                    </td>
                    <td className="px-2 py-1.5 text-right">{d.search_lag ? formatCount(d.search_lag.lag, locale) : '—'}</td>
                    <td className="whitespace-nowrap px-2 py-1.5">
                      {d.backup?.newest_at ? `${formatIso(d.backup.newest_at)} · ${formatBytesShort(d.backup.newest_size_bytes ?? 0)}` : '—'}
                    </td>
                    <td className="px-2 py-1.5">
                      {Object.entries(d.jobs_by_status).map(([status, n]) => `${status} ${n}`).join(', ') || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}

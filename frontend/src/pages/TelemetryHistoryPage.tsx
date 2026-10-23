import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ArrowPathIcon, TableCellsIcon, ChartBarIcon, ExclamationTriangleIcon } from '@heroicons/react/24/outline'
import {
  ApiError,
  telemetryApi,
  TelemetryAnomalyReport,
  TelemetryHistory,
  TelemetryJobReport,
} from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import { isSuperAdmin } from '../api/roles'
import TimeSeriesChart, { ChartBand, ChartSeries, SeriesSlot } from '../components/telemetry/TimeSeriesChart'
import AnomalyList from '../components/telemetry/AnomalyList'
import JobReportPanel from '../components/telemetry/JobReportPanel'
import { bucketGrid, formatNumber, makeTimeFormat } from '../components/telemetry/format'

/** Range presets (hours), shortest first. */
const RANGES: Array<{ key: string; hours: number }> = [
  { key: '1h', hours: 1 },
  { key: '6h', hours: 6 },
  { key: '24h', hours: 24 },
  { key: '7d', hours: 168 },
  { key: '30d', hours: 720 },
]

/** Container roles in fixed slots: color follows the role, never the current ranking. */
const CONTAINER_ROLES: Array<{ role: string; match: RegExp; slot: SeriesSlot }> = [
  { role: 'backend', match: /backend/, slot: 1 },
  { role: 'worker', match: /worker/, slot: 2 },
  { role: 'mongodb', match: /mongo/, slot: 3 },
  { role: 'embedder', match: /embedder|ollama/, slot: 4 },
]

const RANGE_KEY = 'recallhub_telemetry_range'
const REFRESH_MS = 30_000

/** Values of `points` aligned to `grid` by bucket start (null where a bucket has no point). */
function align<T extends { t: string }>(grid: number[], points: T[], pick: (p: T) => number | null): Array<number | null> {
  const byTime = new Map(points.map(p => [Date.parse(p.t), p]))
  return grid.map(t => {
    const p = byTime.get(t)
    return p ? pick(p) : null
  })
}

export default function TelemetryHistoryPage() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { user, isLoading: authLoading } = useAuth()
  const superAdmin = isSuperAdmin(user)
  const fmt = useMemo(() => makeTimeFormat(i18n.language || 'en'), [i18n.language])

  const [hours, setHours] = useState<number>(() => {
    try {
      const saved = Number(localStorage.getItem(RANGE_KEY))
      return RANGES.some(r => r.hours === saved) ? saved : 6
    } catch {
      return 6
    }
  })
  const [history, setHistory] = useState<TelemetryHistory | null>(null)
  const [anomalies, setAnomalies] = useState<TelemetryAnomalyReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [hover, setHover] = useState<number | null>(null)
  const [showTable, setShowTable] = useState(false)
  const [jobReport, setJobReport] = useState<TelemetryJobReport | null>(null)
  const [jobLoading, setJobLoading] = useState<string | null>(null)

  useEffect(() => {
    if (!authLoading && !superAdmin) navigate('/dashboard')
  }, [authLoading, superAdmin, navigate])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [h, a] = await Promise.all([telemetryApi.history(hours), telemetryApi.anomalies(hours)])
      setHistory(h)
      setAnomalies(a)
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError && err.status === 503 ? t('telemetry.disabled') : t('telemetry.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [hours, t])

  useEffect(() => {
    if (!superAdmin) return
    load()
    try {
      localStorage.setItem(RANGE_KEY, String(hours))
    } catch {
      /* ignore */
    }
    if (hours > 6) return
    const timer = setInterval(load, REFRESH_MS)
    return () => clearInterval(timer)
  }, [superAdmin, load, hours])

  const openJob = async (jobId: string) => {
    setJobLoading(jobId)
    try {
      setJobReport(await telemetryApi.job(jobId))
    } catch {
      setError(t('telemetry.jobFailed'))
    } finally {
      setJobLoading(null)
    }
  }

  const downloadReport = async () => {
    if (!jobReport) return
    const blob = await telemetryApi.jobReportHtml(jobReport.job_id)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `telemetry-${jobReport.job_id}.html`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }

  const view = useMemo(() => {
    if (!history) return null
    const grid = bucketGrid(history.start, history.end, history.bucket_seconds)
    const host = (pick: (p: TelemetryHistory['host'][number]) => number | null) => align(grid, history.host, pick)
    const bands: ChartBand[] = history.phases.map(p => ({
      start: Date.parse(p.start),
      end: Date.parse(p.end),
      label: `${t('telemetry.ingestion')}: ${p.phase}`,
    }))
    const containerSeries: ChartSeries[] = CONTAINER_ROLES.map(({ role, match, slot }) => {
      const names = Object.keys(history.containers).filter(n => match.test(n))
      const sums = grid.map(() => null as number | null)
      for (const name of names) {
        align(grid, history.containers[name], p => p.cpu_cores).forEach((v, i) => {
          if (v !== null) sums[i] = (sums[i] ?? 0) + v
        })
      }
      return { key: role, label: t(`telemetry.containers.${role}`), slot, values: sums }
    })
    // Counts: a bucket without events is zero, not missing (activity does not depend on the agent).
    const files = align(grid, history.activity, p => p.files_ok + p.files_failed + p.files_other).map(v => v ?? 0)
    const chats = align(grid, history.activity, p => p.chats).map(v => v ?? 0)
    return {
      grid,
      bands,
      charts: [
        { key: 'cpu', title: t('telemetry.chart.cpu'), unit: '%', yMax: 100,
          series: [{ key: 'cpu', label: t('telemetry.series.cpu'), slot: 1 as SeriesSlot, values: host(p => p.cpu_pct) }] },
        { key: 'mem', title: t('telemetry.chart.memory'), unit: '%', yMax: 100,
          series: [{ key: 'mem', label: t('telemetry.series.ram'), slot: 1 as SeriesSlot, values: host(p => p.mem_pct) }] },
        { key: 'gpu', title: t('telemetry.chart.gpu'), unit: '%', yMax: 100,
          series: [{ key: 'gpu', label: t('telemetry.series.gpu'), slot: 1 as SeriesSlot, values: host(p => p.gpu_util_pct) }] },
        { key: 'temp', title: t('telemetry.chart.temperature'), unit: '°C',
          series: [
            { key: 'cpu_temp', label: t('telemetry.series.cpuPackage'), slot: 1 as SeriesSlot, values: host(p => p.cpu_temp_c) },
            { key: 'gpu_temp', label: t('telemetry.series.gpu'), slot: 2 as SeriesSlot, values: host(p => p.gpu_temp_c) },
          ] },
        { key: 'power', title: t('telemetry.chart.power'), unit: 'W',
          series: [
            { key: 'cpu_power', label: t('telemetry.series.cpuPackage'), slot: 1 as SeriesSlot, values: host(p => p.cpu_power_w) },
            { key: 'gpu_power', label: t('telemetry.series.gpu'), slot: 2 as SeriesSlot, values: host(p => p.gpu_power_w) },
          ] },
        { key: 'containers', title: t('telemetry.chart.containers'), unit: t('telemetry.units.cores'), series: containerSeries },
        { key: 'activity', title: t('telemetry.chart.activity'), unit: t('telemetry.units.perBucket', { bucket: history.bucket_seconds }),
          integer: true,
          series: [
            { key: 'files', label: t('telemetry.series.files'), slot: 1 as SeriesSlot, values: files },
            { key: 'chats', label: t('telemetry.series.chats'), slot: 2 as SeriesSlot, values: chats },
          ] },
      ],
    }
  }, [history, t])

  if (!superAdmin) return null

  const unitFormat = (unit: string) => (v: number) =>
    unit === '%' ? `${Math.round(v)}%` : unit === '°C' ? `${Math.round(v)}°` : unit === 'W' ? `${Math.round(v)} W` : v >= 10 ? String(Math.round(v)) : v.toFixed(1)

  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">{t('telemetry.title')}</h1>
          <p className="text-sm text-gray-600 dark:text-gray-400">{t('telemetry.subtitle')}</p>
        </div>
        <button type="button" onClick={load} disabled={loading}
          className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-700">
          <ArrowPathIcon className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          {t('telemetry.refresh')}
        </button>
      </div>

      {/* One filter row above everything it scopes */}
      <div className="flex flex-wrap items-center gap-2">
        <div role="radiogroup" aria-label={t('telemetry.range')} className="flex rounded-lg border border-gray-200 p-0.5 dark:border-gray-700">
          {RANGES.map(r => (
            <button key={r.key} type="button" role="radio" aria-checked={hours === r.hours} onClick={() => setHours(r.hours)}
              className={`rounded-md px-3 py-1 text-sm transition-colors ${hours === r.hours
                ? 'bg-primary-100 font-semibold text-primary-800 dark:bg-primary-900/50 dark:text-primary-200'
                : 'text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700'}`}>
              {t(`telemetry.ranges.${r.key}`)}
            </button>
          ))}
        </div>
        <button type="button" onClick={() => setShowTable(v => !v)} aria-pressed={showTable}
          className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1 text-sm text-gray-700 hover:bg-gray-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-700">
          {showTable ? <ChartBarIcon className="h-4 w-4" /> : <TableCellsIcon className="h-4 w-4" />}
          {showTable ? t('telemetry.showCharts') : t('telemetry.showTable')}
        </button>
        {history && (
          <span className="text-xs text-gray-500 dark:text-gray-400">
            {t('telemetry.samplesInRange', { count: history.agent_samples, bucket: history.bucket_seconds })}
          </span>
        )}
      </div>

      {error && <div className="rounded-2xl bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/30 dark:text-red-300">{error}</div>}

      {history && history.agent_samples === 0 && (
        <div className="flex items-start gap-2 rounded-2xl bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-900/30 dark:text-amber-200">
          <ExclamationTriangleIcon className="h-5 w-5 shrink-0" aria-hidden="true" />
          {t('telemetry.noAgent')}
        </div>
      )}

      {!history && loading && <div className="py-12 text-center text-gray-500">{t('common.loading')}</div>}

      {history && view && (
        <div className={`space-y-5 transition-opacity ${loading ? 'opacity-60' : ''}`}>
          <section className="rounded-2xl bg-surface p-4 shadow-elevation-1 dark:bg-gray-800">
            <h2 className="mb-2 text-base font-semibold text-gray-900 dark:text-gray-100">{t('telemetry.anomalies')}</h2>
            <AnomalyList anomalies={anomalies?.anomalies ?? []} formatTime={fmt.iso} />
          </section>

          {showTable ? (
            <section className="overflow-x-auto rounded-2xl bg-surface p-4 shadow-elevation-1 dark:bg-gray-800">
              <table className="min-w-full text-sm" aria-label={t('telemetry.tableLabel')}>
                <thead>
                  <tr>
                    {['time', 'cpu', 'ram', 'gpu', 'cpuTemp', 'gpuTemp', 'cpuPower', 'gpuPower', 'files', 'chats'].map(k => (
                      <th key={k} className="whitespace-nowrap px-2 py-1.5 text-left text-xs font-medium text-gray-500 dark:text-gray-400">{t(`telemetry.col.${k}`)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {view.grid.map((ms, i) => {
                    const c = view.charts
                    const v = (chart: number, s = 0) => formatNumber(c[chart].series[s].values[i], 1)
                    return (
                      <tr key={ms} className="border-t border-gray-100 dark:border-gray-700" style={{ fontVariantNumeric: 'tabular-nums' }}>
                        <td className="whitespace-nowrap px-2 py-1 text-gray-700 dark:text-gray-300">{fmt.axis(ms, 0)}</td>
                        <td className="px-2 py-1 text-right">{v(0)}</td>
                        <td className="px-2 py-1 text-right">{v(1)}</td>
                        <td className="px-2 py-1 text-right">{v(2)}</td>
                        <td className="px-2 py-1 text-right">{v(3, 0)}</td>
                        <td className="px-2 py-1 text-right">{v(3, 1)}</td>
                        <td className="px-2 py-1 text-right">{v(4, 0)}</td>
                        <td className="px-2 py-1 text-right">{v(4, 1)}</td>
                        <td className="px-2 py-1 text-right">{formatNumber(c[6].series[0].values[i], 0)}</td>
                        <td className="px-2 py-1 text-right">{formatNumber(c[6].series[1].values[i], 0)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </section>
          ) : (
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              {view.charts.map(chart => (
                <TimeSeriesChart
                  key={chart.key}
                  title={chart.title}
                  unit={chart.unit}
                  times={view.grid}
                  series={chart.series}
                  bands={view.bands}
                  yMax={chart.yMax}
                  integer={'integer' in chart ? chart.integer : false}
                  hoverIndex={hover}
                  onHoverIndex={setHover}
                  format={'integer' in chart && chart.integer ? (v: number) => String(Math.round(v)) : unitFormat(chart.unit)}
                  emptyLabel={t('telemetry.noData')}
                  formatTime={fmt.axis}
                />
              ))}
            </div>
          )}

          <section className="overflow-x-auto rounded-2xl bg-surface p-4 shadow-elevation-1 dark:bg-gray-800">
            <h2 className="mb-2 text-base font-semibold text-gray-900 dark:text-gray-100">{t('telemetry.jobs')}</h2>
            {history.jobs.length === 0 ? (
              <p className="text-sm text-gray-500 dark:text-gray-400">{t('telemetry.noJobs')}</p>
            ) : (
              <table className="min-w-full text-sm">
                <thead>
                  <tr>
                    {['job', 'status', 'tenant', 'first', 'last', 'files', 'failed', ''].map((k, i) => (
                      <th key={i} className="whitespace-nowrap px-2 py-1.5 text-left text-xs font-medium text-gray-500 dark:text-gray-400">{k ? t(`telemetry.col.${k}`) : ''}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {history.jobs.map(j => (
                    <tr key={j.job_id} className="border-t border-gray-100 dark:border-gray-700" style={{ fontVariantNumeric: 'tabular-nums' }}>
                      <td className="max-w-48 truncate px-2 py-1.5 font-mono text-xs" title={j.job_id}>{j.job_id}</td>
                      <td className="px-2 py-1.5">{j.status ?? '—'}</td>
                      <td className="px-2 py-1.5">{j.tenant ?? '—'}</td>
                      <td className="whitespace-nowrap px-2 py-1.5">{fmt.iso(j.first_ts)}</td>
                      <td className="whitespace-nowrap px-2 py-1.5">{fmt.iso(j.last_ts)}</td>
                      <td className="px-2 py-1.5 text-right">{j.files}</td>
                      <td className="px-2 py-1.5 text-right">{j.failed}</td>
                      <td className="px-2 py-1.5 text-right">
                        <button type="button" onClick={() => openJob(j.job_id)} disabled={jobLoading === j.job_id}
                          className="text-sm font-medium text-primary-600 hover:underline disabled:opacity-50 dark:text-primary-400">
                          {jobLoading === j.job_id ? t('common.loading') : t('telemetry.openJob')}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          {jobReport && (
            <JobReportPanel report={jobReport} formatAxis={fmt.axis} formatIso={fmt.iso}
              onClose={() => setJobReport(null)} onDownload={downloadReport} />
          )}
        </div>
      )}
    </div>
  )
}

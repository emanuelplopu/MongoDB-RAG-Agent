import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowDownTrayIcon, XMarkIcon } from '@heroicons/react/24/outline'
import { TelemetryJobReport } from '../../api/client'
import AnomalyList from './AnomalyList'
import TimeSeriesChart from './TimeSeriesChart'
import { bucketGrid, formatBytesShort, formatDurationMs, formatNumber } from './format'

interface JobReportPanelProps {
  report: TelemetryJobReport
  formatAxis: (ms: number, span: number) => string
  formatIso: (iso: string) => string
  onClose: () => void
  onDownload: () => Promise<void>
}

const th = 'px-2 py-1.5 text-left text-xs font-medium text-gray-500 dark:text-gray-400 whitespace-nowrap'
const td = 'px-2 py-1.5 text-sm text-gray-800 dark:text-gray-200 whitespace-nowrap'
const tdNum = `${td} text-right`
const numStyle = { fontVariantNumeric: 'tabular-nums' as const }

/** Correlated report of one ingestion job: phases with resources, step timings, file types, files, anomalies. */
export default function JobReportPanel({ report, formatAxis, formatIso, onClose, onDownload }: JobReportPanelProps) {
  const { t } = useTranslation()
  const [hover, setHover] = useState<number | null>(null)
  const [downloading, setDownloading] = useState(false)
  // Dense grid over the job window: a bucket without finished files counts zero.
  const bucketMs = report.throughput_bucket_seconds * 1000
  const times = bucketGrid(report.start, new Date(Date.parse(report.end) + bucketMs).toISOString(), report.throughput_bucket_seconds)
  const byTime = new Map(report.throughput.map(p => [Date.parse(p.t), p]))
  const filesOk = times.map(t => byTime.get(t)?.files_ok ?? 0)
  const filesFailed = times.map(t => byTime.get(t)?.files_failed ?? 0)

  const download = async () => {
    setDownloading(true)
    try {
      await onDownload()
    } finally {
      setDownloading(false)
    }
  }

  return (
    <section className="space-y-4 rounded-2xl bg-surface p-4 shadow-elevation-1 dark:bg-gray-800" aria-label={t('telemetry.job.title', { id: report.job_id })}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="truncate text-lg font-semibold text-gray-900 dark:text-gray-100">{t('telemetry.job.title', { id: report.job_id })}</h2>
          <p className="text-sm text-gray-600 dark:text-gray-300" style={numStyle}>
            {report.running ? t('telemetry.job.running') : (report.status ?? '—')} · {formatIso(report.start)} – {formatIso(report.end)}
            {' · '}{t('telemetry.job.files', { count: report.files })}
            {report.files_per_hour != null && ` · ${t('telemetry.job.perHour', { rate: report.files_per_hour })}`}
          </p>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {Object.entries(report.outcomes).map(([k, v]) => `${t(`telemetry.outcome.${k}`, k)} ${v}`).join(' · ')}
            {report.total_pages ? ` · ${t('telemetry.job.pages', { count: report.total_pages })}` : ''}
            {report.total_bytes ? ` · ${formatBytesShort(report.total_bytes)}` : ''}
            {' · '}{t('telemetry.job.samples', { count: report.agent_samples })}
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={download} disabled={downloading}
            className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-primary-600 disabled:opacity-50">
            <ArrowDownTrayIcon className="h-4 w-4" />
            {t('telemetry.job.download')}
          </button>
          <button type="button" onClick={onClose} aria-label={t('common.close')}
            className="rounded-lg p-1.5 text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700">
            <XMarkIcon className="h-5 w-5" />
          </button>
        </div>
      </div>

      <div>
        <h3 className="mb-2 text-sm font-semibold text-gray-700 dark:text-gray-200">{t('telemetry.anomalies')}</h3>
        <AnomalyList anomalies={report.anomalies} formatTime={formatIso} />
      </div>

      {times.length > 1 && (
        <TimeSeriesChart
          title={t('telemetry.job.throughput')}
          unit={t('telemetry.units.filesPerBucket')}
          times={times}
          series={[
            { key: 'ok', label: t('telemetry.outcome.ok'), slot: 1, values: filesOk },
            { key: 'failed', label: t('telemetry.outcome.error'), slot: 2, values: filesFailed },
          ]}
          hoverIndex={hover}
          onHoverIndex={setHover}
          integer
          format={v => String(Math.round(v))}
          emptyLabel={t('telemetry.noData')}
          formatTime={formatAxis}
        />
      )}

      <div className="overflow-x-auto">
        <h3 className="mb-1 text-sm font-semibold text-gray-700 dark:text-gray-200">{t('telemetry.job.phases')}</h3>
        <table className="min-w-full">
          <thead><tr>
            {['phase', 'start', 'duration', 'cpu', 'ramMax', 'gpu', 'gpuTempMax', 'cpuPower', 'gpuPower'].map(k => (
              <th key={k} className={th}>{t(`telemetry.col.${k}`)}</th>
            ))}
          </tr></thead>
          <tbody>
            {report.phases.map((p, i) => (
              <tr key={i} className="border-t border-gray-100 dark:border-gray-700">
                <td className={td}>{p.phase}</td>
                <td className={td} style={numStyle}>{formatIso(p.start)}</td>
                <td className={tdNum} style={numStyle}>{formatDurationMs(p.duration_s * 1000)}</td>
                <td className={tdNum} style={numStyle}>{formatNumber(p.host.cpu_pct)}</td>
                <td className={tdNum} style={numStyle}>{formatNumber(p.host.mem_pct_max)}</td>
                <td className={tdNum} style={numStyle}>{formatNumber(p.host.gpu_util_pct)}</td>
                <td className={tdNum} style={numStyle}>{formatNumber(p.host.gpu_temp_c_max)}</td>
                <td className={tdNum} style={numStyle}>{formatNumber(p.host.cpu_power_w)}</td>
                <td className={tdNum} style={numStyle}>{formatNumber(p.host.gpu_power_w)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="overflow-x-auto">
        <h3 className="mb-1 text-sm font-semibold text-gray-700 dark:text-gray-200">{t('telemetry.job.steps')}</h3>
        <table className="min-w-full">
          <thead><tr>
            {['step', 'files', 'median', 'p95', 'max', 'total', 'share'].map(k => <th key={k} className={th}>{t(`telemetry.col.${k}`)}</th>)}
          </tr></thead>
          <tbody>
            {report.steps.map(s => (
              <tr key={s.step} className="border-t border-gray-100 dark:border-gray-700">
                <td className={td}>{t(`telemetry.step.${s.step}`, s.step)}</td>
                <td className={tdNum} style={numStyle}>{s.files}</td>
                <td className={tdNum} style={numStyle}>{formatDurationMs(s.p50_ms)}</td>
                <td className={tdNum} style={numStyle}>{formatDurationMs(s.p95_ms)}</td>
                <td className={tdNum} style={numStyle}>{formatDurationMs(s.max_ms)}</td>
                <td className={tdNum} style={numStyle}>{formatDurationMs(s.total_ms)}</td>
                <td className={td}>
                  <div className="flex items-center gap-2">
                    <div className="viz-root h-2 w-24 rounded-full bg-gray-100 dark:bg-gray-700" aria-hidden="true">
                      <div className="h-2 rounded-full" style={{ width: `${Math.min(s.share_pct ?? 0, 100)}%`, background: 'var(--viz-series-1)' }} />
                    </div>
                    <span style={numStyle}>{s.share_pct != null ? `${s.share_pct}%` : '—'}</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="overflow-x-auto">
        <h3 className="mb-1 text-sm font-semibold text-gray-700 dark:text-gray-200">{t('telemetry.job.types')}</h3>
        <table className="min-w-full">
          <thead><tr>
            {['type', 'files', 'ok', 'failed', 'empty', 'median', 'p95', 'perPage'].map(k => <th key={k} className={th}>{t(`telemetry.col.${k}`)}</th>)}
          </tr></thead>
          <tbody>
            {report.by_ext.map(x => (
              <tr key={x.ext} className="border-t border-gray-100 dark:border-gray-700">
                <td className={td}>{x.ext}</td>
                <td className={tdNum} style={numStyle}>{x.files}</td>
                <td className={tdNum} style={numStyle}>{x.ok}</td>
                <td className={tdNum} style={numStyle}>{x.failed}</td>
                <td className={tdNum} style={numStyle}>{x.empty}</td>
                <td className={tdNum} style={numStyle}>{formatDurationMs(x.p50_ms)}</td>
                <td className={tdNum} style={numStyle}>{formatDurationMs(x.p95_ms)}</td>
                <td className={tdNum} style={numStyle}>{formatDurationMs(x.ms_per_page)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="overflow-x-auto">
        <h3 className="mb-1 text-sm font-semibold text-gray-700 dark:text-gray-200">{t('telemetry.job.slowest')}</h3>
        <table className="min-w-full">
          <thead><tr>
            {['file', 'outcome', 'duration', 'pages', 'steps'].map(k => <th key={k} className={th}>{t(`telemetry.col.${k}`)}</th>)}
          </tr></thead>
          <tbody>
            {report.slowest.map((f, i) => (
              <tr key={`${f.path}-${i}`} className="border-t border-gray-100 dark:border-gray-700">
                <td className={`${td} max-w-xs truncate`} title={f.path}>{f.path}</td>
                <td className={td}>{t(`telemetry.outcome.${f.outcome}`, f.outcome ?? '—')}</td>
                <td className={tdNum} style={numStyle}>{formatDurationMs(f.duration_ms)}</td>
                <td className={tdNum} style={numStyle}>{f.pages ?? '—'}</td>
                <td className={`${td} text-xs text-gray-500 dark:text-gray-400`}>
                  {Object.entries(f.steps).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${t(`telemetry.step.${k}`, k)} ${formatDurationMs(v)}`).join(' · ')}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {report.unfinished.length > 0 && (
        <div>
          <h3 className="mb-1 text-sm font-semibold text-gray-700 dark:text-gray-200">{t('telemetry.job.unfinished')}</h3>
          <ul className="space-y-0.5 break-all font-mono text-xs text-gray-700 dark:text-gray-300">
            {report.unfinished.map((f, i) => <li key={i}>{f.ts ? `${formatIso(f.ts)} · ` : ''}{f.path}</li>)}
          </ul>
        </div>
      )}
    </section>
  )
}

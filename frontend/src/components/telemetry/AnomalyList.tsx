import { useTranslation } from 'react-i18next'
import { ExclamationCircleIcon, ExclamationTriangleIcon, InformationCircleIcon, CheckCircleIcon } from '@heroicons/react/24/outline'
import { TelemetryAnomaly, TelemetrySeverity } from '../../api/client'

/** Status colors are reserved for severity and always paired with an icon and a label. */
const SEVERITY_STYLE: Record<TelemetrySeverity, { icon: typeof ExclamationCircleIcon; color: string }> = {
  critical: { icon: ExclamationCircleIcon, color: '#d03b3b' },
  warning: { icon: ExclamationTriangleIcon, color: '#d99400' },
  info: { icon: InformationCircleIcon, color: '#898781' },
}

/** What an anomaly's count counts (host rules count samples). */
const COUNT_UNIT: Record<string, string> = {
  failed_files: 'files',
  cancelled_files: 'files',
  unfinished_files: 'files',
  slow_files: 'files',
  empty_files: 'files',
  container_restart: 'containers',
  container_memory: 'containers',
  sampling_gap: 'gaps',
  search_index_unhealthy: 'indexes',
  embedding_coverage: 'databases',
  embedding_mix: 'databases',
  search_lag: 'databases',
  backup_stale: 'databases',
  chunks_flat: 'databases',
  connection_churn: 'intervals',
  process_silent: 'processes',
}

/** Renders one example (dict of mixed values) as "key: value · key: value". */
function exampleText(example: Record<string, unknown>): string {
  return Object.entries(example)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(' · ')
}

interface AnomalyListProps {
  anomalies: TelemetryAnomaly[]
  formatTime: (iso: string) => string
}

/** Detected problems, most severe first, with episodes and examples as evidence. */
export default function AnomalyList({ anomalies, formatTime }: AnomalyListProps) {
  const { t } = useTranslation()

  if (anomalies.length === 0) {
    return (
      <p className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
        <CheckCircleIcon className="h-5 w-5 text-[#0ca30c]" aria-hidden="true" />
        {t('telemetry.noAnomalies')}
      </p>
    )
  }

  return (
    <ul className="space-y-2">
      {anomalies.map(a => {
        const style = SEVERITY_STYLE[a.severity] ?? SEVERITY_STYLE.info
        const Icon = style.icon
        return (
          <li key={`${a.kind}-${a.title}`} className="rounded-xl border border-gray-200 p-3 dark:border-gray-700">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <Icon className="h-5 w-5 shrink-0" style={{ color: style.color }} aria-hidden="true" />
              <span className="text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-300">
                {t(`telemetry.severity.${a.severity}`)}
              </span>
              <span className="font-medium text-gray-900 dark:text-gray-100">{a.title}</span>
              <span className="text-xs text-gray-500 dark:text-gray-400">
                {t(`telemetry.countUnit.${COUNT_UNIT[a.kind] ?? 'samples'}`, { count: a.count })}
              </span>
            </div>
            <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">{a.detail}</p>
            {a.episodes.length > 0 && (
              <ul className="mt-1.5 flex flex-wrap gap-1.5">
                {a.episodes.slice(0, 6).map((e, i) => (
                  <li key={i} className="rounded-md bg-gray-100 px-1.5 py-0.5 text-xs text-gray-700 dark:bg-gray-700/60 dark:text-gray-200"
                    style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {formatTime(e.start)}{e.end !== e.start ? ` – ${formatTime(e.end)}` : ''}
                    {e.peak !== null ? ` · ${t('telemetry.peak')} ${e.peak}` : ''}
                    {e.detail ? ` · ${e.detail}` : ''}
                  </li>
                ))}
                {a.episodes.length > 6 && (
                  <li className="px-1 text-xs text-gray-500">{t('telemetry.moreEpisodes', { count: a.episodes.length - 6 })}</li>
                )}
              </ul>
            )}
            {a.examples.length > 0 && (
              <details className="mt-1.5">
                <summary className="cursor-pointer text-xs text-primary-600 dark:text-primary-400">
                  {t('telemetry.examples', { count: a.examples.length })}
                </summary>
                <ul className="mt-1 space-y-0.5 break-all font-mono text-[11px] text-gray-600 dark:text-gray-300">
                  {a.examples.slice(0, 15).map((ex, i) => (
                    <li key={i}>{exampleText(ex)}</li>
                  ))}
                </ul>
              </details>
            )}
          </li>
        )
      })}
    </ul>
  )
}

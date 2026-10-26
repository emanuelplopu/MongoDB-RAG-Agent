import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  CpuChipIcon,
  XMarkIcon,
  MinusIcon,
  CircleStackIcon,
  ServerStackIcon,
  FireIcon,
  ClockIcon,
  CubeIcon,
  ArrowPathIcon,
  ChatBubbleLeftRightIcon,
  ChartBarIcon,
  SparklesIcon,
  ArchiveBoxIcon,
  CommandLineIcon,
} from '@heroicons/react/24/outline'
import { useAuth } from '../contexts/AuthContext'
import { isSuperAdmin } from '../api/roles'
import { systemApi, telemetryApi, HostStats, HostActivityState, TelemetryOverview, OverviewBackup, OverviewProcess } from '../api/client'
import { LocalizedLink } from './LocalizedLink'
import { formatCount, formatDurationMs, formatSeconds } from './telemetry/format'

/** How often the widget re-polls the host-stats endpoint (ms). */
const POLL_INTERVAL_MS = 4000

/** How often the widget re-polls the telemetry overview (database, anomalies, liveness, backups) (ms). */
const OVERVIEW_INTERVAL_MS = 60_000

/** A backup older than this (seconds) is flagged. */
const BACKUP_STALE_SECONDS = 7 * 86400

/** localStorage key remembering whether the widget is minimised to a chip. */
const COLLAPSED_KEY = 'recallhub_hoststats_collapsed'

/** Format a byte count as a compact human string (base 1024). */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return '—'
  if (bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  const exponent = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  const value = bytes / Math.pow(1024, exponent)
  return `${value.toFixed(value >= 100 || exponent === 0 ? 0 : 1)} ${units[exponent]}`
}

/** Format a percentage (0-100) with no decimals. */
function formatPercent(pct: number | null | undefined): string {
  return pct == null || !Number.isFinite(pct) ? '—' : `${Math.round(pct)}%`
}

/** Format a temperature in degrees Celsius. */
function formatTemp(celsius: number | null | undefined): string {
  return celsius == null || !Number.isFinite(celsius) ? '—' : `${Math.round(celsius)}°C`
}

/** Format a power draw in watts. */
function formatWatts(watts: number | null | undefined): string {
  return watts == null || !Number.isFinite(watts) ? '—' : `${watts.toFixed(0)} W`
}

/** Humanise an uptime in seconds, e.g. "2d 11h" or "36m". */
export function formatUptime(seconds: number | null | undefined): string {
  return formatSeconds(seconds)
}

/** "rag-backend-quellex" -> "backend-quellex" (the compose prefix is noise in a tiny card). */
function shortContainerName(name: string): string {
  return name.replace(/^rag-/, '')
}

/** Join the parts that have a value: ["18%", null, "36 W"] -> "18% · 36 W". */
function joinParts(parts: Array<string | null>): string {
  const present = parts.filter((p): p is string => p !== null && p !== '—')
  return present.length > 0 ? present.join(' · ') : '—'
}

/** "3 / 10 (30%)" or "9,181 / 361,819 (2.5%)" — the share keeps a decimal while it is small. */
function formatProgress(processed: number | null | undefined, total: number | null | undefined, locale?: string): string {
  if (!total) return ''
  const done = processed ?? 0
  const share = (done / total) * 100
  return ` ${formatCount(done, locale)} / ${formatCount(total, locale)} (${share < 10 ? share.toFixed(1) : Math.round(share)}%)`
}

type Tone = 'default' | 'warn'

interface MetricRowProps {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: string
  /** Muted second line under the value. */
  sub?: string | null
  title?: string
  /** Let the value wrap over several lines instead of truncating. */
  wrap?: boolean
  tone?: Tone
}

function MetricRow({ icon: Icon, label, value, sub, title, wrap = false, tone = 'default' }: MetricRowProps) {
  const valueColor = tone === 'warn' ? 'text-amber-700 dark:text-amber-300' : 'text-gray-800 dark:text-gray-200'
  return (
    <div className={`flex ${wrap ? 'items-start' : 'items-center'} justify-between gap-3 py-0.5`} title={title}>
      <span className="flex shrink-0 items-center gap-1.5 text-gray-500 dark:text-gray-400">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </span>
      <span className={`min-w-0 text-right font-mono tabular-nums ${wrap ? 'whitespace-normal break-words' : 'truncate'} ${valueColor}`}>
        {value}
        {sub && <span className="block text-[10px] font-normal text-gray-400 dark:text-gray-500">{sub}</span>}
      </span>
    </div>
  )
}

function activityLabel(a: HostActivityState, t: (key: string) => string, locale?: string): string | null {
  if (a.job_id) {
    const stage = a.step || a.phase
    return `${t('hostStats.ingesting')}${formatProgress(a.processed_files, a.total_files, locale)}${stage ? ` · ${stage}` : ''}`
  }
  return a.chat_active ? t('hostStats.chatActive') : null
}

/** "1,550 files/h · ETA 9d 11h" for the running job: the pace from the overview, the ETA from the
 * shared per-extension estimator (the same figure the ingestion page shows). */
function paceLabel(process: OverviewProcess | undefined, t: (key: string) => string, locale?: string): string | null {
  if (!process || (process.files_per_hour == null && process.eta_seconds == null)) return null
  const pace = process.files_per_hour != null
    ? `${formatCount(process.files_per_hour, locale)} ${t('hostStats.filesPerHour')}`
    : ''
  const eta = process.eta_seconds != null ? `${t('hostStats.eta')} ${formatSeconds(process.eta_seconds)}` : ''
  return [pace, eta].filter(Boolean).join(' · ')
}

/** Tooltip: the extensions with the most files left and the pace each is estimated at. */
export function etaBreakdown(process: OverviewProcess | undefined, locale?: string): string | null {
  const rows = process?.eta_by_extension ?? []
  if (rows.length === 0) return null
  return rows.slice(0, 8).map(r =>
    `${r.extension || '(none)'}: ${formatCount(r.remaining, locale)} left` +
    (r.seconds != null ? ` ≈ ${formatSeconds(r.seconds)}` : ' (no pace yet)')
  ).join('\n')
}

/** The backup that deserves attention most: none, then empty, then oldest. */
export function worstBackup(backups: OverviewBackup[]): OverviewBackup | null {
  if (backups.length === 0) return null
  const rank = (b: OverviewBackup) =>
    b.newest_at === null ? 3 : !b.size_bytes ? 2 : (b.age_seconds ?? 0) > BACKUP_STALE_SECONDS ? 1 : 0
  return [...backups].sort((a, b) => rank(b) - rank(a) || (b.age_seconds ?? 0) - (a.age_seconds ?? 0))[0]
}

function backupStale(b: OverviewBackup): boolean {
  return b.newest_at === null || !b.size_bytes || (b.age_seconds ?? 0) > BACKUP_STALE_SECONDS
}

/**
 * Floating, superadmin-only live machine-stats widget.
 *
 * Polls `GET /api/v1/system/host-stats` every few seconds. With the Windows host agent running
 * (`source === 'host-agent'`) it shows true host CPU, RAM, disk, CPU/GPU temperature and power,
 * uptime, what is running and the busiest containers. While the agent is offline the API falls
 * back to its container view (the Docker Desktop VM); the badge and a note say so.
 *
 * A second, slower poll of `GET /api/v1/telemetry/overview` adds health at a glance: database
 * size and chunk count, loaded LLMs, worker liveness, chat latency, the newest backup and an
 * anomaly badge. Those rows simply stay away while the overview is unavailable.
 * Self-gates on {@link isSuperAdmin}; collapsible to a chip (persisted) and dismissible for the session.
 */
export default function HostStatsWidget() {
  const { t, i18n } = useTranslation()
  const locale = i18n?.language || undefined
  const { user } = useAuth()

  const [stats, setStats] = useState<HostStats | null>(null)
  const [overview, setOverview] = useState<TelemetryOverview | null>(null)
  const [error, setError] = useState(false)
  const [loading, setLoading] = useState(true)
  const [dismissed, setDismissed] = useState(false)
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(COLLAPSED_KEY) === 'true'
    } catch {
      return false
    }
  })

  const inFlight = useRef(false)
  const overviewInFlight = useRef(false)
  const isSuper = isSuperAdmin(user)

  const fetchStats = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    try {
      const data = await systemApi.hostStats()
      setStats(data)
      setError(false)
    } catch {
      setError(true)
    } finally {
      setLoading(false)
      inFlight.current = false
    }
  }, [])

  const fetchOverview = useCallback(async () => {
    if (overviewInFlight.current) return
    overviewInFlight.current = true
    try {
      setOverview(await telemetryApi.overview())
    } catch {
      /* keep the last overview; the host rows do not depend on it */
    } finally {
      overviewInFlight.current = false
    }
  }, [])

  // Poll while mounted and visible to a superadmin.
  useEffect(() => {
    if (!isSuper || dismissed) return
    fetchStats()
    const timer = setInterval(fetchStats, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [isSuper, dismissed, fetchStats])

  useEffect(() => {
    if (!isSuper || dismissed) return
    fetchOverview()
    const timer = setInterval(fetchOverview, OVERVIEW_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [isSuper, dismissed, fetchOverview])

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSED_KEY, String(collapsed))
    } catch {
      /* ignore storage failures */
    }
  }, [collapsed])

  if (!isSuper || dismissed) return null

  // Collapsed: a small unobtrusive chip that restores the full card on click.
  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        className="fixed bottom-[88px] right-4 z-30 flex items-center gap-1.5 rounded-full border border-gray-200 bg-white/90 px-3 py-1.5 text-xs font-medium text-gray-600 shadow-md backdrop-blur-sm transition-colors hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800/90 dark:text-gray-300 dark:hover:bg-gray-700"
        aria-label={t('hostStats.expand')}
        title={t('hostStats.title')}
      >
        <CpuChipIcon className="h-4 w-4" />
        <span className="font-mono tabular-nums">{formatPercent(stats?.cpu_percent)}</span>
      </button>
    )
  }

  const fromHost = stats?.source === 'host-agent'
  const cpuTempMissing = fromHost && stats?.cpu_temp_c == null
  const processes = overview?.processes ?? []
  const activity = (stats?.activity ?? [])
    .map(a => {
      const process = processes.find(p => p.job_id && p.job_id === a.job_id)
      return { a, label: activityLabel(a, t, locale), pace: paceLabel(process, t, locale), breakdown: etaBreakdown(process, locale) }
    })
    .filter((x): x is { a: HostActivityState; label: string; pace: string | null; breakdown: string | null } => x.label !== null)
  const busiest = (stats?.containers ?? []).filter(c => (c.cpu_cores ?? 0) > 0).slice(0, 3)

  // Overview rows (each absent until its data exists).
  const db = overview?.db ?? null
  const dbValue = db
    ? joinParts([
        `${formatCount(db.chunks, locale)} ${t('hostStats.chunks')}`,
        formatBytes(db.storage_bytes),
        db.search_lag != null && db.search_lag > 0 ? `${t('hostStats.indexLag')} ${formatCount(db.search_lag, locale)}` : null,
        db.embedding_coverage_pct != null && db.embedding_coverage_pct < 100 ? `${Math.round(db.embedding_coverage_pct)}% ${t('hostStats.embedded')}` : null,
      ])
    : null
  const llm = overview?.llm ?? null
  const llmValue = llm?.sampled_at
    ? llm.models.length > 0
      ? llm.models.map(m => joinParts([m.name, m.vram_bytes != null ? `${formatBytes(m.vram_bytes)} ${t('hostStats.vram')}` : null])).join(', ')
      : t('hostStats.llmIdle')
    : null
  // Worker rows always, API rows only when the process went quiet (its own activity row covers the rest).
  const processRows = processes.filter(p => !p.online || (p.source === 'worker' && !p.job_id))
  const chat = overview?.chat ?? null
  const backups = overview?.backups ?? []
  const worst = worstBackup(backups)
  const anomalies = overview?.anomalies ?? null
  const showBadge = anomalies !== null && anomalies.critical + anomalies.warning > 0

  return (
    <div
      className="fixed bottom-[88px] right-4 z-30 w-[21rem] select-none rounded-xl border border-gray-200 bg-white/95 p-3 text-xs shadow-lg backdrop-blur-sm dark:border-gray-700 dark:bg-gray-800/95"
      role="region"
      aria-label={t('hostStats.title')}
    >
      {/* Header */}
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5 font-semibold text-gray-700 dark:text-gray-200">
          <CpuChipIcon className="h-4 w-4 shrink-0" />
          <span className="truncate">{t('hostStats.title')}</span>
        </span>
        <div className="flex shrink-0 items-center gap-1">
          {showBadge && anomalies && (
            <LocalizedLink
              to="/system/telemetry"
              className={`flex items-center gap-1 rounded-full px-1.5 py-0.5 font-mono text-[10px] font-semibold tabular-nums ${
                anomalies.critical > 0
                  ? 'bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-300'
                  : 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
              }`}
              title={anomalies.top.join(' · ')}
              aria-label={t('hostStats.anomalies', { critical: anomalies.critical, warning: anomalies.warning })}
            >
              {anomalies.critical > 0 && <span>{anomalies.critical}</span>}
              {anomalies.critical > 0 && anomalies.warning > 0 && <span aria-hidden="true">·</span>}
              {anomalies.warning > 0 && (
                <span className={anomalies.critical > 0 ? 'text-amber-700 dark:text-amber-300' : undefined}>{anomalies.warning}</span>
              )}
            </LocalizedLink>
          )}
          {stats && (
            <span
              className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
                fromHost
                  ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300'
                  : 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
              }`}
              title={fromHost ? t('hostStats.sourceHostTooltip') : t('hostStats.agentOffline')}
            >
              {fromHost ? t('hostStats.sourceHost') : t('hostStats.sourceContainer')}
            </span>
          )}
          <button
            type="button"
            onClick={() => setCollapsed(true)}
            className="rounded-sm p-0.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300"
            aria-label={t('hostStats.collapse')}
            title={t('hostStats.collapse')}
          >
            <MinusIcon className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => setDismissed(true)}
            className="rounded-sm p-0.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700 dark:hover:text-gray-300"
            aria-label={t('hostStats.dismiss')}
            title={t('hostStats.dismiss')}
          >
            <XMarkIcon className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* Body */}
      {loading && !stats ? (
        <div className="py-3 text-center text-gray-400 dark:text-gray-500">{t('hostStats.loading')}</div>
      ) : error && !stats ? (
        <div className="py-3 text-center text-amber-600 dark:text-amber-400">{t('hostStats.error')}</div>
      ) : stats ? (
        <div className="space-y-0.5">
          <MetricRow
            icon={CpuChipIcon}
            label={t('hostStats.cpu')}
            value={joinParts([formatPercent(stats.cpu_percent), stats.cpu_power_watts != null ? formatWatts(stats.cpu_power_watts) : null])}
            title={t('hostStats.cpuTooltip')}
          />
          <MetricRow
            icon={ServerStackIcon}
            label={t('hostStats.ram')}
            value={`${formatBytes(stats.ram_used_bytes)} / ${formatBytes(stats.ram_total_bytes)}`}
            title={formatPercent(stats.ram_percent)}
          />
          <MetricRow
            icon={CircleStackIcon}
            label={t('hostStats.disk')}
            value={`${formatBytes(stats.disk_used_bytes)} / ${formatBytes(stats.disk_total_bytes)}`}
            title={stats.disk_path ? `${stats.disk_path} · ${formatPercent(stats.disk_percent)}` : undefined}
          />
          {stats.gpu_name && (
            <MetricRow
              icon={CpuChipIcon}
              label={t('hostStats.gpu')}
              value={joinParts([
                formatPercent(stats.gpu_percent),
                formatBytes(stats.vram_used_bytes),
                stats.gpu_power_watts != null ? formatWatts(stats.gpu_power_watts) : null,
              ])}
              title={stats.gpu_name}
            />
          )}
          <MetricRow
            icon={FireIcon}
            label={t('hostStats.temp')}
            value={joinParts([
              stats.cpu_temp_c != null ? `CPU ${formatTemp(stats.cpu_temp_c)}` : null,
              stats.gpu_temp_c != null ? `GPU ${formatTemp(stats.gpu_temp_c)}` : null,
            ])}
            title={cpuTempMissing ? t('hostStats.cpuTempMissing') : t('hostStats.tempTooltip')}
          />
          <MetricRow icon={ClockIcon} label={t('hostStats.uptime')} value={formatUptime(stats.uptime_seconds)} />

          {activity.map(({ a, label, pace, breakdown }) => (
            <MetricRow
              key={`${a.tenant}:${a.source}`}
              icon={a.job_id ? ArrowPathIcon : ChatBubbleLeftRightIcon}
              label={a.tenant === 'shared' ? t('hostStats.sharedTenant') : (a.tenant ?? t('hostStats.activity'))}
              value={label}
              sub={pace}
              wrap
              title={[a.current_file, breakdown].filter(Boolean).join('\n') || undefined}
            />
          ))}

          {(dbValue || llmValue || processRows.length > 0 || (chat && chat.requests > 0) || db) && (
            <div className="mt-1 border-t border-gray-100 pt-1 dark:border-gray-700">
              {db && dbValue && (
                <MetricRow
                  icon={CircleStackIcon}
                  label={t('hostStats.db')}
                  value={dbValue}
                  wrap
                  tone={db.search_indexes_ok === false ? 'warn' : 'default'}
                  title={t('hostStats.dbTooltip', {
                    documents: formatCount(db.documents, locale),
                    data: formatBytes(db.data_bytes),
                    index: formatBytes(db.index_bytes),
                    oplog: formatBytes(db.oplog_bytes),
                    age: formatSeconds(db.age_seconds),
                  })}
                />
              )}
              {llmValue && (
                <MetricRow icon={SparklesIcon} label={t('hostStats.llm')} value={llmValue} wrap title={llm?.sampled_at ?? undefined} />
              )}
              {processRows.map(p => (
                <MetricRow
                  key={p.key}
                  icon={CommandLineIcon}
                  label={p.source === 'worker' ? t('hostStats.worker') : t('hostStats.api')}
                  value={p.online ? t('hostStats.idle') : joinParts([t('hostStats.offline'), p.age_seconds != null ? formatSeconds(p.age_seconds) : null])}
                  tone={p.online ? 'default' : 'warn'}
                  title={p.last_seen_at ? t('hostStats.lastSeen', { age: formatSeconds(p.age_seconds) }) : p.key}
                />
              ))}
              {chat && chat.requests > 0 && (
                <MetricRow
                  icon={ChatBubbleLeftRightIcon}
                  label={t('hostStats.chat')}
                  value={`${formatCount(chat.requests, locale)} ${t('hostStats.requests')} · p95 ${formatDurationMs(chat.p95_ms)}`}
                  title={t('hostStats.chatTooltip', { hours: chat.hours, errors: chat.errors, p50: formatDurationMs(chat.p50_ms) })}
                />
              )}
              {db && (
                <MetricRow
                  icon={ArchiveBoxIcon}
                  label={t('hostStats.backup')}
                  value={
                    worst
                      ? worst.newest_at === null
                        ? t('hostStats.backupNone')
                        : `${t('hostStats.ago', { age: formatSeconds(worst.age_seconds) })} · ${formatBytes(worst.size_bytes ?? 0)}`
                      : t('hostStats.backupNone')
                  }
                  tone={!worst || backupStale(worst) ? 'warn' : 'default'}
                  title={backups
                    .map(b => `${b.database}: ${b.newest_at ? `${t('hostStats.ago', { age: formatSeconds(b.age_seconds) })} · ${formatBytes(b.size_bytes ?? 0)}` : t('hostStats.backupNone')}`)
                    .join('\n') || undefined}
                />
              )}
            </div>
          )}

          {busiest.length > 0 && (
            <div className="mt-1 border-t border-gray-100 pt-1 dark:border-gray-700">
              {busiest.map(c => (
                <MetricRow
                  key={c.name}
                  icon={CubeIcon}
                  label={shortContainerName(c.name)}
                  value={`${(c.cpu_cores ?? 0).toFixed(2)} ${t('hostStats.cores')} · ${formatBytes(c.mem_bytes)}`}
                  title={c.name}
                />
              ))}
            </div>
          )}

          {error && (
            <div className="pt-1 text-[10px] text-amber-600 dark:text-amber-400">{t('hostStats.stale')}</div>
          )}
          {!fromHost && (
            <div className="mt-1.5 border-t border-gray-100 pt-1.5 text-[10px] leading-tight text-gray-400 dark:border-gray-700 dark:text-gray-500">
              {t('hostStats.caveatContainer')}
            </div>
          )}
          <div className="mt-1.5 flex justify-end">
            <LocalizedLink
              to="/system/telemetry"
              className="flex items-center gap-1 text-[11px] font-medium text-primary-600 hover:underline dark:text-primary-400"
            >
              <ChartBarIcon className="h-3.5 w-3.5" />
              {t('hostStats.history')}
            </LocalizedLink>
          </div>
        </div>
      ) : null}
    </div>
  )
}

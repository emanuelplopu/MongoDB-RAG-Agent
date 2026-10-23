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
} from '@heroicons/react/24/outline'
import { useAuth } from '../contexts/AuthContext'
import { isSuperAdmin } from '../api/roles'
import { systemApi, HostStats, HostActivityState } from '../api/client'
import { LocalizedLink } from './LocalizedLink'
import { formatBytes, formatUptime } from './formatUnits'

/** How often the widget re-polls the host-stats endpoint (ms). */
const POLL_INTERVAL_MS = 4000

/** localStorage key remembering whether the widget is minimised to a chip. */
const COLLAPSED_KEY = 'recallhub_hoststats_collapsed'

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

/** "rag-backend-quellex" -> "backend-quellex" (the compose prefix is noise in a tiny card). */
function shortContainerName(name: string): string {
  return name.replace(/^rag-/, '')
}

/** Join the parts that have a value: ["18%", null, "36 W"] -> "18% · 36 W". */
function joinParts(parts: Array<string | null>): string {
  const present = parts.filter((p): p is string => p !== null && p !== '—')
  return present.length > 0 ? present.join(' · ') : '—'
}

interface MetricRowProps {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: string
  title?: string
}

function MetricRow({ icon: Icon, label, value, title }: MetricRowProps) {
  return (
    <div className="flex items-center justify-between gap-3 py-0.5" title={title}>
      <span className="flex items-center gap-1.5 text-gray-500 dark:text-gray-400">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </span>
      <span className="font-mono text-gray-800 dark:text-gray-200 tabular-nums truncate">{value}</span>
    </div>
  )
}

function activityLabel(a: HostActivityState, t: (key: string) => string): string | null {
  if (a.job_id) {
    const progress = a.total_files ? ` ${a.processed_files ?? 0}/${a.total_files}` : ''
    const stage = a.step || a.phase
    return `${t('hostStats.ingesting')}${progress}${stage ? ` · ${stage}` : ''}`
  }
  return a.chat_active ? t('hostStats.chatActive') : null
}

/**
 * Floating, superadmin-only live machine-stats widget.
 *
 * Polls `GET /api/v1/system/host-stats` every few seconds. With the Windows host agent running
 * (`source === 'host-agent'`) it shows true host CPU, RAM, disk, CPU/GPU temperature and power,
 * uptime, what is running and the busiest containers. While the agent is offline the API falls
 * back to its container view (the Docker Desktop VM); the badge and a note say so. Self-gates on
 * {@link isSuperAdmin}; collapsible to a chip (persisted) and dismissible for the session.
 */
export default function HostStatsWidget() {
  const { t } = useTranslation()
  const { user } = useAuth()

  const [stats, setStats] = useState<HostStats | null>(null)
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

  // Poll while mounted and visible to a superadmin.
  useEffect(() => {
    if (!isSuper || dismissed) return
    // eslint-disable-next-line react-hooks/set-state-in-effect -- first poll; fetchStats updates state synchronously only when a request is already in flight
    fetchStats()
    const timer = setInterval(fetchStats, POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [isSuper, dismissed, fetchStats])

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
  const activity = (stats?.activity ?? [])
    .map(a => ({ a, label: activityLabel(a, t) }))
    .filter((x): x is { a: HostActivityState; label: string } => x.label !== null)
  const busiest = (stats?.containers ?? []).filter(c => (c.cpu_cores ?? 0) > 0).slice(0, 3)

  return (
    <div
      className="fixed bottom-[88px] right-4 z-30 w-64 select-none rounded-xl border border-gray-200 bg-white/95 p-3 text-xs shadow-lg backdrop-blur-sm dark:border-gray-700 dark:bg-gray-800/95"
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

          {activity.map(({ a, label }) => (
            <MetricRow
              key={`${a.tenant}:${a.source}`}
              icon={a.job_id ? ArrowPathIcon : ChatBubbleLeftRightIcon}
              label={a.tenant === 'shared' ? t('hostStats.sharedTenant') : (a.tenant ?? t('hostStats.activity'))}
              value={label}
              title={a.current_file ?? undefined}
            />
          ))}

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

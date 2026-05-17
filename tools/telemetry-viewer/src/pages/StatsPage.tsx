import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useDataSource } from '../api'
import { TelemetryRecord } from '../types/telemetry'

interface ComputedStats {
  totalRecords: number
  avgLatencyMs: number
  totalTokens: number
  errorRate: number
  uniqueSessions: number
  uniqueModels: string[]
  latencyBuckets: { label: string; count: number; color: string }[]
  modelDistribution: { model: string; count: number; percentage: number }[]
  tokensPerModel: { model: string; promptTokens: number; responseTokens: number }[]
  phaseDurations: { phase: string; avgMs: number }[]
  recordsByDate: { date: string; count: number }[]
  piiStats: { type: string; count: number }[]
}

function computeStats(records: TelemetryRecord[]): ComputedStats {
  const totalRecords = records.length
  if (totalRecords === 0) {
    return {
      totalRecords: 0, avgLatencyMs: 0, totalTokens: 0, errorRate: 0,
      uniqueSessions: 0, uniqueModels: [], latencyBuckets: [],
      modelDistribution: [], tokensPerModel: [], phaseDurations: [],
      recordsByDate: [], piiStats: [],
    }
  }

  // Avg latency
  const latencies = records.map(r => r.total_duration_ms || r.response_latency_ms || 0)
  const avgLatencyMs = latencies.reduce((a, b) => a + b, 0) / totalRecords

  // Total tokens
  const totalTokens = records.reduce((sum, r) => {
    const recTokens = (r.prompt_tokens || 0) + (r.response_tokens || 0)
    return sum + recTokens
  }, 0)

  // Error rate
  const errorCount = records.filter(r => {
    if (r.llm_calls && r.llm_calls.length > 0) {
      return r.llm_calls.some(c => !c.success)
    }
    return false
  }).length
  const errorRate = (errorCount / totalRecords) * 100

  // Unique sessions
  const uniqueSessions = new Set(records.map(r => r.session_id).filter(Boolean)).size

  // Unique models
  const uniqueModels = [...new Set(
    records.map(r => r.orchestrator_model).filter(Boolean)
  )]

  // Latency buckets
  const bucketDefs = [
    { label: '< 1s', max: 1000, color: 'bg-green-500' },
    { label: '1-2s', max: 2000, color: 'bg-green-400' },
    { label: '2-5s', max: 5000, color: 'bg-yellow-400' },
    { label: '5-10s', max: 10000, color: 'bg-orange-400' },
    { label: '> 10s', max: Infinity, color: 'bg-red-400' },
  ]
  const latencyBuckets = bucketDefs.map((bucket, i) => {
    const min = i === 0 ? 0 : bucketDefs[i - 1].max
    const count = latencies.filter(l => l >= min && l < bucket.max).length
    return { label: bucket.label, count, color: bucket.color }
  })

  // Model distribution
  const modelCounts: Record<string, number> = {}
  records.forEach(r => {
    const model = r.orchestrator_model || 'unknown'
    modelCounts[model] = (modelCounts[model] || 0) + 1
  })
  const modelDistribution = Object.entries(modelCounts)
    .map(([model, count]) => ({
      model,
      count,
      percentage: (count / totalRecords) * 100,
    }))
    .sort((a, b) => b.count - a.count)

  // Tokens per model
  const tokensByModel: Record<string, { prompt: number; response: number }> = {}
  records.forEach(r => {
    const model = r.orchestrator_model || 'unknown'
    if (!tokensByModel[model]) tokensByModel[model] = { prompt: 0, response: 0 }
    tokensByModel[model].prompt += r.prompt_tokens || 0
    tokensByModel[model].response += r.response_tokens || 0
  })
  const tokensPerModel = Object.entries(tokensByModel)
    .map(([model, t]) => ({ model, promptTokens: t.prompt, responseTokens: t.response }))
    .sort((a, b) => (b.promptTokens + b.responseTokens) - (a.promptTokens + a.responseTokens))

  // Phase durations
  const phaseAcc: Record<string, { total: number; count: number }> = {}
  records.forEach(r => {
    if (r.phase_metrics) {
      r.phase_metrics.forEach(pm => {
        if (!phaseAcc[pm.phase]) phaseAcc[pm.phase] = { total: 0, count: 0 }
        phaseAcc[pm.phase].total += pm.duration_ms
        phaseAcc[pm.phase].count += 1
      })
    }
  })
  const phaseDurations = Object.entries(phaseAcc)
    .map(([phase, d]) => ({ phase, avgMs: Math.round(d.total / d.count) }))
    .sort((a, b) => b.avgMs - a.avgMs)

  // Records by date
  const dateCounts: Record<string, number> = {}
  records.forEach(r => {
    if (r.timestamp) {
      const date = r.timestamp.slice(0, 10)
      dateCounts[date] = (dateCounts[date] || 0) + 1
    }
  })
  const recordsByDate = Object.entries(dateCounts)
    .map(([date, count]) => ({ date, count }))
    .sort((a, b) => a.date.localeCompare(b.date))

  // PII stats
  const piiCounts: Record<string, number> = {}
  records.forEach(r => {
    if (r.entity_mapping) {
      Object.values(r.entity_mapping).forEach(entityType => {
        const type = entityType.toUpperCase()
        piiCounts[type] = (piiCounts[type] || 0) + 1
      })
    }
  })
  const piiStats = Object.entries(piiCounts)
    .map(([type, count]) => ({ type, count }))
    .sort((a, b) => b.count - a.count)

  return {
    totalRecords, avgLatencyMs, totalTokens, errorRate,
    uniqueSessions, uniqueModels, latencyBuckets,
    modelDistribution, tokensPerModel, phaseDurations,
    recordsByDate, piiStats,
  }
}

// --- Chart Components ---

const PHASE_COLORS = [
  'bg-accent-blue', 'bg-accent-purple', 'bg-accent-green',
  'bg-accent-orange', 'bg-accent-teal', 'bg-accent-yellow', 'bg-accent-red',
]
const PHASE_HEX = ['#7aa2f7', '#bb9af7', '#9ece6a', '#ff9e64', '#73daca', '#e0af68', '#f7768e']
const MODEL_COLORS = ['#7aa2f7', '#bb9af7', '#9ece6a', '#ff9e64', '#73daca', '#e0af68', '#f7768e', '#7dcfff']

function LatencyChart({ buckets, title }: { buckets: ComputedStats['latencyBuckets']; title: string }) {
  const maxCount = Math.max(...buckets.map(b => b.count), 1)
  return (
    <div className="bg-gray-50 dark:bg-surface-light rounded-xl p-5 border border-gray-200 dark:border-gray-700/50">
      <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-4">{title}</h3>
      <div className="space-y-3">
        {buckets.map(bucket => (
          <div key={bucket.label} className="flex items-center gap-3">
            <span className="text-xs text-gray-500 dark:text-gray-400 w-12 shrink-0">{bucket.label}</span>
            <div className="flex-1 bg-gray-100 dark:bg-surface rounded-sm h-6 overflow-hidden">
              <div
                className={`${bucket.color} h-full rounded-sm transition-all duration-500`}
                style={{ width: `${(bucket.count / maxCount) * 100}%` }}
              />
            </div>
            <span className="text-xs font-mono text-gray-700 dark:text-gray-300 w-8 text-right">{bucket.count}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function ModelChart({ distribution, title }: { distribution: ComputedStats['modelDistribution']; title: string }) {
  if (distribution.length === 0) return null

  // Build conic-gradient
  let cumulative = 0
  const segments = distribution.map((item, i) => {
    const start = cumulative
    cumulative += item.percentage
    const color = MODEL_COLORS[i % MODEL_COLORS.length]
    return `${color} ${start}% ${cumulative}%`
  })
  const gradient = `conic-gradient(${segments.join(', ')})`

  return (
    <div className="bg-gray-50 dark:bg-surface-light rounded-xl p-5 border border-gray-200 dark:border-gray-700/50">
      <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-4">{title}</h3>
      <div className="flex flex-col items-center gap-4">
        <div className="relative w-40 h-40">
          <div
            className="w-full h-full rounded-full"
            style={{ background: gradient }}
          />
          <div className="absolute inset-0 flex items-center justify-center">
            <div className="w-24 h-24 rounded-full bg-gray-50 dark:bg-surface-light" />
          </div>
        </div>
        <div className="w-full space-y-2">
          {distribution.map((item, i) => (
            <div key={item.model} className="flex items-center gap-2 text-xs">
              <div
                className="w-3 h-3 rounded-full shrink-0"
                style={{ backgroundColor: MODEL_COLORS[i % MODEL_COLORS.length] }}
              />
              <span className="text-gray-700 dark:text-gray-300 truncate flex-1">{item.model}</span>
              <span className="text-gray-500 dark:text-gray-400 font-mono">{item.percentage.toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function TokensPerModelChart({ data, title }: { data: ComputedStats['tokensPerModel']; title: string }) {
  const { t } = useTranslation()
  if (data.length === 0) return null
  const maxTokens = Math.max(...data.map(d => d.promptTokens + d.responseTokens), 1)

  return (
    <div className="bg-gray-50 dark:bg-surface-light rounded-xl p-5 border border-gray-200 dark:border-gray-700/50">
      <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-4">{title}</h3>
      <div className="space-y-4">
        {data.map(item => (
          <div key={item.model}>
            <div className="flex items-center justify-between mb-1">
              <span className="text-xs text-gray-700 dark:text-gray-300 truncate">{item.model}</span>
              <span className="text-xs font-mono text-gray-500 dark:text-gray-400">
                {item.promptTokens.toLocaleString()} / {item.responseTokens.toLocaleString()}
              </span>
            </div>
            <div className="flex gap-1 h-6">
              <div
                className="bg-accent-blue rounded-sm h-full transition-all duration-500"
                style={{ width: `${(item.promptTokens / maxTokens) * 100}%` }}
                title={`Prompt: ${item.promptTokens.toLocaleString()}`}
              />
              <div
                className="bg-accent-purple rounded-sm h-full transition-all duration-500"
                style={{ width: `${(item.responseTokens / maxTokens) * 100}%` }}
                title={`Response: ${item.responseTokens.toLocaleString()}`}
              />
            </div>
          </div>
        ))}
        <div className="flex gap-4 pt-2 border-t border-gray-200 dark:border-gray-700/50">
          <div className="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
            <div className="w-3 h-3 rounded-sm bg-accent-blue" /> {t('common.prompt')}
          </div>
          <div className="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
            <div className="w-3 h-3 rounded-sm bg-accent-purple" /> {t('common.response')}
          </div>
        </div>
      </div>
    </div>
  )
}

function PhaseDurationChart({ phases, title }: { phases: ComputedStats['phaseDurations']; title: string }) {
  if (phases.length === 0) return null
  const totalMs = phases.reduce((sum, p) => sum + p.avgMs, 0)

  return (
    <div className="bg-gray-50 dark:bg-surface-light rounded-xl p-5 border border-gray-200 dark:border-gray-700/50">
      <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-4">{title}</h3>
      {/* Stacked bar */}
      <div className="flex h-8 rounded-md overflow-hidden mb-3">
        {phases.map((phase, i) => (
          <div
            key={phase.phase}
            className={`${PHASE_COLORS[i % PHASE_COLORS.length]} h-full transition-all duration-500`}
            style={{ width: `${(phase.avgMs / totalMs) * 100}%` }}
            title={`${phase.phase}: avg ${phase.avgMs}ms`}
          />
        ))}
      </div>
      {/* Legend */}
      <div className="flex flex-wrap gap-3">
        {phases.map((phase, i) => (
          <div key={phase.phase} className="flex items-center gap-1.5 text-xs">
            <div
              className="w-3 h-3 rounded-sm shrink-0"
              style={{ backgroundColor: PHASE_HEX[i % PHASE_HEX.length] }}
            />
            <span className="text-gray-700 dark:text-gray-300">{phase.phase}</span>
            <span className="text-gray-500 font-mono">{phase.avgMs}ms</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function RecordsOverTimeChart({ data, title }: { data: ComputedStats['recordsByDate']; title: string }) {
  if (data.length === 0) return null
  const maxCount = Math.max(...data.map(d => d.count), 1)

  return (
    <div className="bg-gray-50 dark:bg-surface-light rounded-xl p-5 border border-gray-200 dark:border-gray-700/50">
      <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-4">{title}</h3>
      <div className="space-y-2">
        {data.map(item => (
          <div key={item.date} className="flex items-center gap-3">
            <span className="text-xs text-gray-500 dark:text-gray-400 w-20 shrink-0 font-mono">{item.date}</span>
            <div className="flex-1 bg-gray-100 dark:bg-surface rounded-sm h-6 overflow-hidden">
              <div
                className="bg-accent-teal h-full rounded-sm transition-all duration-500"
                style={{ width: `${(item.count / maxCount) * 100}%` }}
              />
            </div>
            <span className="text-xs font-mono text-gray-700 dark:text-gray-300 w-8 text-right">{item.count}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function PiiStatsChart({ data, title }: { data: ComputedStats['piiStats']; title: string }) {
  if (data.length === 0) return null
  const maxCount = Math.max(...data.map(d => d.count), 1)

  return (
    <div className="bg-gray-50 dark:bg-surface-light rounded-xl p-5 border border-gray-200 dark:border-gray-700/50">
      <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-4">{title}</h3>
      <div className="space-y-3">
        {data.map(item => (
          <div key={item.type} className="flex items-center gap-3">
            <span className="text-xs text-gray-500 dark:text-gray-400 w-20 shrink-0 font-mono">{item.type}</span>
            <div className="flex-1 bg-gray-100 dark:bg-surface rounded-sm h-6 overflow-hidden">
              <div
                className="bg-accent-orange h-full rounded-sm transition-all duration-500"
                style={{ width: `${(item.count / maxCount) * 100}%` }}
              />
            </div>
            <span className="text-xs font-mono text-gray-700 dark:text-gray-300 w-8 text-right">{item.count}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// --- Summary Card ---

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-gray-50 dark:bg-surface-light rounded-xl p-5 border border-gray-200 dark:border-gray-700/50">
      <div className="text-2xl font-bold text-accent-blue">{value}</div>
      <div className="text-sm text-gray-500 dark:text-gray-400 mt-1">{label}</div>
    </div>
  )
}

// --- Main Page ---

export default function StatsPage() {
  const { state } = useDataSource()
  const { t } = useTranslation()

  const stats = useMemo(() => computeStats(state.records), [state.records])

  if (state.records.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-gray-500 dark:text-gray-400">
        <div className="text-center">
          <p className="text-2xl mb-2">📊 {t('stats.title')}</p>
          <p>{t('stats.empty')}</p>
        </div>
      </div>
    )
  }

  const formatLatency = (ms: number) => {
    if (ms < 1000) return `${Math.round(ms)}ms`
    return `${(ms / 1000).toFixed(1)}s`
  }

  return (
    <div className="h-full overflow-y-auto p-6 space-y-6">
      {/* Summary Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        <StatCard label={t('stats.cards.totalRecords')} value={stats.totalRecords.toLocaleString()} />
        <StatCard label={t('stats.cards.avgLatency')} value={formatLatency(stats.avgLatencyMs)} />
        <StatCard label={t('stats.cards.totalTokens')} value={stats.totalTokens.toLocaleString()} />
        <StatCard label={t('stats.cards.errorRate')} value={`${stats.errorRate.toFixed(1)}%`} />
        <StatCard label={t('stats.cards.sessions')} value={stats.uniqueSessions} />
        <StatCard label={t('stats.cards.models')} value={stats.uniqueModels.length} />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <LatencyChart buckets={stats.latencyBuckets} title={t('stats.charts.latencyDist')} />
        <ModelChart distribution={stats.modelDistribution} title={t('stats.charts.modelDist')} />
        <TokensPerModelChart data={stats.tokensPerModel} title={t('stats.charts.tokenUsage')} />
        <PhaseDurationChart phases={stats.phaseDurations} title={t('stats.charts.phaseDuration')} />
        <RecordsOverTimeChart data={stats.recordsByDate} title={t('stats.charts.recordsOverTime')} />
        <PiiStatsChart data={stats.piiStats} title={t('stats.charts.piiStats')} />
      </div>
    </div>
  )
}

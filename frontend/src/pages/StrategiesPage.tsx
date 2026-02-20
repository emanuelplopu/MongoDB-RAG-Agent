import { useState, useEffect, useCallback } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  ArrowPathIcon,
  CpuChipIcon,
  ScaleIcon,
  SparklesIcon,
  CodeBracketIcon,
  DocumentTextIcon,
  UserGroupIcon,
  ChevronRightIcon,
} from '@heroicons/react/24/outline'
import { strategiesApi, StrategyInfo, StrategyStats, StrategyDetail } from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import { useLanguage } from '../contexts/LanguageContext'

// Domain icons mapping
const DOMAIN_ICONS: Record<string, typeof CpuChipIcon> = {
  general: CpuChipIcon,
  software_dev: CodeBracketIcon,
  legal: DocumentTextIcon,
  hr: UserGroupIcon,
}

// Domain colors
const DOMAIN_COLORS: Record<string, string> = {
  general: 'bg-blue-100 dark:bg-blue-900/50 text-blue-600',
  software_dev: 'bg-purple-100 dark:bg-purple-900/50 text-purple-600',
  legal: 'bg-amber-100 dark:bg-amber-900/50 text-amber-600',
  hr: 'bg-green-100 dark:bg-green-900/50 text-green-600',
}

export default function StrategiesPage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { language } = useLanguage()
  const { user, isLoading: authLoading } = useAuth()

  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  const [metrics, setMetrics] = useState<Record<string, StrategyStats>>({})
  const [selectedStrategy, setSelectedStrategy] = useState<StrategyDetail | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [timeWindow, setTimeWindow] = useState<number | undefined>(24) // Default 24 hours

  const fetchData = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const [strategiesList, allMetrics] = await Promise.all([
        strategiesApi.list(),
        strategiesApi.getAllMetrics(timeWindow),
      ])
      setStrategies(strategiesList)
      
      // Convert metrics array to map by strategy_id
      const metricsMap: Record<string, StrategyStats> = {}
      for (const m of allMetrics) {
        metricsMap[m.strategy_id] = m
      }
      setMetrics(metricsMap)
    } catch (err) {
      console.error('Error fetching strategies:', err)
      setError(t('strategies.failedToLoad', 'Failed to load strategies'))
    } finally {
      setIsLoading(false)
    }
  }, [t, timeWindow])

  useEffect(() => {
    if (!authLoading && (!user || !user.is_admin)) {
      navigate(`/${language}/dashboard`)
    }
  }, [user, authLoading, navigate, language])

  useEffect(() => {
    if (!authLoading && user?.is_admin) {
      fetchData()
    }
  }, [authLoading, user, fetchData])

  const handleStrategyClick = async (strategyId: string) => {
    try {
      const detail = await strategiesApi.get(strategyId)
      setSelectedStrategy(detail)
    } catch (err) {
      console.error('Error fetching strategy details:', err)
    }
  }

  if (authLoading || isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  if (!user?.is_admin) return null

  const formatLatency = (ms: number) => {
    if (ms < 1000) return `${ms.toFixed(0)}ms`
    return `${(ms / 1000).toFixed(1)}s`
  }

  const formatPercentage = (value: number) => `${(value * 100).toFixed(1)}%`

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-200">
            {t('strategies.title', 'Agent Strategies')}
          </h2>
          <p className="text-sm text-secondary dark:text-gray-400">
            {t('strategies.subtitle', 'Manage and compare RAG agent strategies')}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Time window selector */}
          <select
            value={timeWindow || 'all'}
            onChange={(e) => setTimeWindow(e.target.value === 'all' ? undefined : parseInt(e.target.value))}
            className="rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 border-none"
          >
            <option value="1">{t('strategies.lastHour', 'Last hour')}</option>
            <option value="24">{t('strategies.last24h', 'Last 24 hours')}</option>
            <option value="168">{t('strategies.lastWeek', 'Last week')}</option>
            <option value="720">{t('strategies.lastMonth', 'Last month')}</option>
            <option value="all">{t('strategies.allTime', 'All time')}</option>
          </select>
          <Link
            to={`/${language}/system/strategies/ab-test`}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white transition-all hover:bg-primary-600"
          >
            <ScaleIcon className="h-4 w-4" />
            {t('strategies.abTest', 'A/B Test')}
          </Link>
          <button
            onClick={fetchData}
            className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 transition-all hover:bg-primary-100 dark:hover:bg-gray-600"
          >
            <ArrowPathIcon className="h-4 w-4" />
            {t('common.refresh', 'Refresh')}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-2xl bg-red-50 dark:bg-red-900/30 p-4 text-red-700 dark:text-red-400">{error}</div>
      )}

      {/* Strategy Cards Grid */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {strategies.map((strategy) => {
          const strategyMetrics = metrics[strategy.id]
          const primaryDomain = strategy.domains[0] || 'general'
          const DomainIcon = DOMAIN_ICONS[primaryDomain] || CpuChipIcon
          const domainColor = DOMAIN_COLORS[primaryDomain] || DOMAIN_COLORS.general

          return (
            <div
              key={strategy.id}
              onClick={() => handleStrategyClick(strategy.id)}
              className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1 cursor-pointer hover:shadow-elevation-2 transition-all"
            >
              {/* Header */}
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className={`rounded-xl p-2 ${domainColor}`}>
                    <DomainIcon className="h-5 w-5" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-primary-900 dark:text-gray-200">
                      {strategy.name}
                    </h3>
                    <p className="text-xs text-secondary dark:text-gray-500">v{strategy.version}</p>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  {strategy.is_default && (
                    <span className="rounded-full bg-primary-100 dark:bg-primary-900/50 px-2 py-0.5 text-xs font-medium text-primary">
                      {t('strategies.default', 'Default')}
                    </span>
                  )}
                  {strategy.is_legacy && (
                    <span className="rounded-full bg-gray-100 dark:bg-gray-700 px-2 py-0.5 text-xs font-medium text-gray-600 dark:text-gray-400">
                      {t('strategies.legacy', 'Legacy')}
                    </span>
                  )}
                </div>
              </div>

              {/* Description */}
              <p className="text-sm text-secondary dark:text-gray-400 mb-4 line-clamp-2">
                {strategy.description}
              </p>

              {/* Tags */}
              <div className="flex flex-wrap gap-1 mb-4">
                {strategy.tags.slice(0, 4).map((tag) => (
                  <span
                    key={tag}
                    className="rounded-full bg-surface-variant dark:bg-gray-700 px-2 py-0.5 text-xs text-secondary dark:text-gray-400"
                  >
                    {tag}
                  </span>
                ))}
                {strategy.tags.length > 4 && (
                  <span className="text-xs text-secondary dark:text-gray-500">
                    +{strategy.tags.length - 4}
                  </span>
                )}
              </div>

              {/* Metrics */}
              {strategyMetrics && (
                <div className="grid grid-cols-3 gap-2 pt-4 border-t border-gray-100 dark:border-gray-700">
                  <div className="text-center">
                    <p className="text-lg font-semibold text-primary-900 dark:text-gray-200">
                      {strategyMetrics.execution_count}
                    </p>
                    <p className="text-xs text-secondary dark:text-gray-500">
                      {t('strategies.executions', 'Executions')}
                    </p>
                  </div>
                  <div className="text-center">
                    <p className="text-lg font-semibold text-primary-900 dark:text-gray-200">
                      {formatLatency(strategyMetrics.avg_latency_ms)}
                    </p>
                    <p className="text-xs text-secondary dark:text-gray-500">
                      {t('strategies.avgLatency', 'Avg Latency')}
                    </p>
                  </div>
                  <div className="text-center">
                    <p className="text-lg font-semibold text-primary-900 dark:text-gray-200">
                      {formatPercentage(strategyMetrics.avg_confidence)}
                    </p>
                    <p className="text-xs text-secondary dark:text-gray-500">
                      {t('strategies.avgConfidence', 'Confidence')}
                    </p>
                  </div>
                </div>
              )}

              {!strategyMetrics && (
                <div className="pt-4 border-t border-gray-100 dark:border-gray-700 text-center">
                  <p className="text-sm text-secondary dark:text-gray-500">
                    {t('strategies.noMetrics', 'No metrics available')}
                  </p>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Strategy Detail Modal */}
      {selectedStrategy && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={() => setSelectedStrategy(null)}
        >
          <div
            className="bg-surface dark:bg-gray-800 rounded-2xl shadow-xl max-w-2xl w-full mx-4 max-h-[80vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Modal Header */}
            <div className="p-6 border-b border-gray-100 dark:border-gray-700">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className={`rounded-xl p-2 ${DOMAIN_COLORS[selectedStrategy.domains[0]] || DOMAIN_COLORS.general}`}>
                    <SparklesIcon className="h-6 w-6" />
                  </div>
                  <div>
                    <h3 className="text-lg font-semibold text-primary-900 dark:text-gray-200">
                      {selectedStrategy.name}
                    </h3>
                    <p className="text-sm text-secondary dark:text-gray-400">
                      v{selectedStrategy.version} by {selectedStrategy.author}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => setSelectedStrategy(null)}
                  className="text-secondary hover:text-primary-900 dark:hover:text-gray-200"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* Modal Content */}
            <div className="p-6 space-y-6">
              {/* Description */}
              <div>
                <h4 className="font-medium text-primary-900 dark:text-gray-200 mb-2">
                  {t('strategies.description', 'Description')}
                </h4>
                <p className="text-secondary dark:text-gray-400">
                  {selectedStrategy.description}
                </p>
              </div>

              {/* Configuration */}
              <div>
                <h4 className="font-medium text-primary-900 dark:text-gray-200 mb-2">
                  {t('strategies.configuration', 'Configuration')}
                </h4>
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-surface-variant dark:bg-gray-700 rounded-xl p-3">
                    <p className="text-xs text-secondary dark:text-gray-500">
                      {t('strategies.maxIterations', 'Max Iterations')}
                    </p>
                    <p className="font-semibold text-primary-900 dark:text-gray-200">
                      {selectedStrategy.config.max_iterations}
                    </p>
                  </div>
                  <div className="bg-surface-variant dark:bg-gray-700 rounded-xl p-3">
                    <p className="text-xs text-secondary dark:text-gray-500">
                      {t('strategies.confidenceThreshold', 'Confidence Threshold')}
                    </p>
                    <p className="font-semibold text-primary-900 dark:text-gray-200">
                      {formatPercentage(selectedStrategy.config.confidence_threshold)}
                    </p>
                  </div>
                  <div className="bg-surface-variant dark:bg-gray-700 rounded-xl p-3">
                    <p className="text-xs text-secondary dark:text-gray-500">
                      {t('strategies.earlyExit', 'Early Exit')}
                    </p>
                    <p className="font-semibold text-primary-900 dark:text-gray-200">
                      {selectedStrategy.config.early_exit_enabled ? t('common.enabled', 'Enabled') : t('common.disabled', 'Disabled')}
                    </p>
                  </div>
                  <div className="bg-surface-variant dark:bg-gray-700 rounded-xl p-3">
                    <p className="text-xs text-secondary dark:text-gray-500">
                      {t('strategies.crossSearchBoost', 'Cross-Search Boost')}
                    </p>
                    <p className="font-semibold text-primary-900 dark:text-gray-200">
                      {selectedStrategy.config.cross_search_boost}x
                    </p>
                  </div>
                </div>
              </div>

              {/* Prompt Previews */}
              <div>
                <h4 className="font-medium text-primary-900 dark:text-gray-200 mb-2">
                  {t('strategies.promptPreviews', 'Prompt Previews')}
                </h4>
                <div className="space-y-2">
                  {Object.entries(selectedStrategy.prompts_preview).map(([key, value]) => (
                    <details key={key} className="bg-surface-variant dark:bg-gray-700 rounded-xl">
                      <summary className="px-4 py-2 cursor-pointer text-sm font-medium text-primary-900 dark:text-gray-200 flex items-center gap-2">
                        <ChevronRightIcon className="h-4 w-4 transition-transform details-open:rotate-90" />
                        {key.charAt(0).toUpperCase() + key.slice(1)} Prompt
                      </summary>
                      <pre className="px-4 pb-3 text-xs text-secondary dark:text-gray-400 whitespace-pre-wrap overflow-x-auto">
                        {value}
                      </pre>
                    </details>
                  ))}
                </div>
              </div>

              {/* Domains & Tags */}
              <div className="flex gap-4">
                <div className="flex-1">
                  <h4 className="font-medium text-primary-900 dark:text-gray-200 mb-2">
                    {t('strategies.domains', 'Domains')}
                  </h4>
                  <div className="flex flex-wrap gap-1">
                    {selectedStrategy.domains.map((domain) => (
                      <span
                        key={domain}
                        className={`rounded-full px-3 py-1 text-xs font-medium ${DOMAIN_COLORS[domain] || DOMAIN_COLORS.general}`}
                      >
                        {domain}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex-1">
                  <h4 className="font-medium text-primary-900 dark:text-gray-200 mb-2">
                    {t('strategies.tags', 'Tags')}
                  </h4>
                  <div className="flex flex-wrap gap-1">
                    {selectedStrategy.tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-full bg-surface-variant dark:bg-gray-600 px-2 py-0.5 text-xs text-secondary dark:text-gray-400"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

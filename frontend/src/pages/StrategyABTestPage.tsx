import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  ArrowPathIcon,
  ScaleIcon,
  ClockIcon,
  CheckCircleIcon,
  SparklesIcon,
  ChartBarIcon,
  ArrowLeftIcon,
  PlayIcon,
} from '@heroicons/react/24/outline'
import {
  strategiesApi,
  sessionsApi,
  StrategyInfo,
  ABCompareResult,
  ResponseScore,
} from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import { useLanguage } from '../contexts/LanguageContext'
import MarkdownRenderer from '../components/MarkdownRenderer'

interface ChatResponse {
  strategy_id: string
  strategy_name: string
  content: string
  latency_ms: number
  isLoading: boolean
  error: string | null
}

// Score bar component for visual representation
function ScoreBar({ score, label, maxScore = 10 }: { score: number; label: string; maxScore?: number }) {
  const percentage = (score / maxScore) * 100
  const getColor = (pct: number) => {
    if (pct >= 80) return 'bg-green-500'
    if (pct >= 60) return 'bg-blue-500'
    if (pct >= 40) return 'bg-yellow-500'
    return 'bg-red-500'
  }

  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-secondary dark:text-gray-400">{label}</span>
        <span className="font-medium text-primary-900 dark:text-gray-200">{score.toFixed(1)}</span>
      </div>
      <div className="h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div
          className={`h-full ${getColor(percentage)} transition-all duration-500`}
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  )
}

// Score card for a single response
function ResponseScoreCard({
  scores,
  strategyName,
  latencyMs,
  isWinner,
}: {
  scores: ResponseScore
  strategyName: string
  latencyMs: number
  isWinner: boolean
}) {
  const { t } = useTranslation()

  return (
    <div className={`rounded-xl p-4 ${isWinner ? 'bg-green-50 dark:bg-green-900/20 ring-2 ring-green-500' : 'bg-surface-variant dark:bg-gray-700'}`}>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <SparklesIcon className="h-5 w-5 text-primary" />
          <span className="font-semibold text-primary-900 dark:text-gray-200">{strategyName}</span>
        </div>
        {isWinner && (
          <span className="flex items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400">
            <CheckCircleIcon className="h-4 w-4" />
            {t('strategies.winner', 'Winner')}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 mb-4">
        <div className="text-center p-2 bg-surface dark:bg-gray-800 rounded-lg">
          <p className="text-2xl font-bold text-primary-900 dark:text-gray-200">{scores.overall.toFixed(1)}</p>
          <p className="text-xs text-secondary dark:text-gray-400">{t('strategies.overallScore', 'Overall')}</p>
        </div>
        <div className="text-center p-2 bg-surface dark:bg-gray-800 rounded-lg">
          <p className="text-2xl font-bold text-primary-900 dark:text-gray-200">{(latencyMs / 1000).toFixed(1)}s</p>
          <p className="text-xs text-secondary dark:text-gray-400">{t('strategies.latency', 'Latency')}</p>
        </div>
      </div>

      <div className="space-y-2">
        <ScoreBar score={scores.quality} label={t('strategies.quality', 'Quality')} />
        <ScoreBar score={scores.hallucination} label={t('strategies.hallucination', 'No Hallucination')} />
        <ScoreBar score={scores.readability} label={t('strategies.readability', 'Readability')} />
        <ScoreBar score={scores.factuality} label={t('strategies.factuality', 'Factuality')} />
        <ScoreBar score={scores.relevance} label={t('strategies.relevance', 'Relevance')} />
      </div>
    </div>
  )
}

export default function StrategyABTestPage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { language } = useLanguage()
  const { user, isLoading: authLoading } = useAuth()

  // State
  const [strategies, setStrategies] = useState<StrategyInfo[]>([])
  const [strategyA, setStrategyA] = useState<string>('')
  const [strategyB, setStrategyB] = useState<string>('')
  const [query, setQuery] = useState('')
  const [isLoading, setIsLoading] = useState(true)
  const [sessionId, setSessionId] = useState<string | null>(null)

  // Response state
  const [responseA, setResponseA] = useState<ChatResponse | null>(null)
  const [responseB, setResponseB] = useState<ChatResponse | null>(null)
  const [comparisonResult, setComparisonResult] = useState<ABCompareResult | null>(null)
  const [isComparing, setIsComparing] = useState(false)

  // Refs
  const queryInputRef = useRef<HTMLTextAreaElement>(null)

  // Fetch strategies on mount
  const fetchStrategies = useCallback(async () => {
    setIsLoading(true)
    try {
      const list = await strategiesApi.list()
      setStrategies(list)
      // Set defaults
      if (list.length >= 2) {
        const defaultStrategy = list.find((s) => s.is_default) || list[0]
        const legacyStrategy = list.find((s) => s.is_legacy) || list[1]
        setStrategyA(defaultStrategy.id)
        setStrategyB(legacyStrategy.id)
      }
    } catch (err) {
      console.error('Error fetching strategies:', err)
    } finally {
      setIsLoading(false)
    }
  }, [])

  // Create session on mount
  const createSession = useCallback(async () => {
    try {
      const session = await sessionsApi.create({ title: 'A/B Test Session' })
      setSessionId(session.id)
    } catch (err) {
      console.error('Error creating session:', err)
    }
  }, [])

  useEffect(() => {
    if (!authLoading && (!user || !user.is_admin)) {
      navigate(`/${language}/dashboard`)
    }
  }, [user, authLoading, navigate, language])

  useEffect(() => {
    if (!authLoading && user?.is_admin) {
      fetchStrategies()
      createSession()
    }
  }, [authLoading, user, fetchStrategies, createSession])

  // Run test with both strategies
  const runTest = async () => {
    if (!query.trim() || !sessionId || !strategyA || !strategyB) return

    // Reset results
    setResponseA({
      strategy_id: strategyA,
      strategy_name: strategies.find((s) => s.id === strategyA)?.name || strategyA,
      content: '',
      latency_ms: 0,
      isLoading: true,
      error: null,
    })
    setResponseB({
      strategy_id: strategyB,
      strategy_name: strategies.find((s) => s.id === strategyB)?.name || strategyB,
      content: '',
      latency_ms: 0,
      isLoading: true,
      error: null,
    })
    setComparisonResult(null)

    // Run strategy A first
    const startA = performance.now()
    try {
      const resultA = await sessionsApi.sendMessage(sessionId, query, {
        agent_mode: 'thinking',
        strategy_id: strategyA,
      })
      const latencyA = performance.now() - startA

      setResponseA({
        strategy_id: strategyA,
        strategy_name: strategies.find((s) => s.id === strategyA)?.name || strategyA,
        content: resultA.assistant_message.content,
        latency_ms: latencyA,
        isLoading: false,
        error: null,
      })
    } catch (err) {
      setResponseA((prev) =>
        prev
          ? { ...prev, isLoading: false, error: err instanceof Error ? err.message : 'Error' }
          : null
      )
    }

    // Run strategy B
    const startB = performance.now()
    try {
      const resultB = await sessionsApi.sendMessage(sessionId, query, {
        agent_mode: 'thinking',
        strategy_id: strategyB,
      })
      const latencyB = performance.now() - startB

      setResponseB({
        strategy_id: strategyB,
        strategy_name: strategies.find((s) => s.id === strategyB)?.name || strategyB,
        content: resultB.assistant_message.content,
        latency_ms: latencyB,
        isLoading: false,
        error: null,
      })
    } catch (err) {
      setResponseB((prev) =>
        prev
          ? { ...prev, isLoading: false, error: err instanceof Error ? err.message : 'Error' }
          : null
      )
    }
  }

  // Compare responses using LLM
  const compareResponses = async () => {
    if (!responseA?.content || !responseB?.content) return

    setIsComparing(true)
    try {
      const result = await strategiesApi.abCompareResponses({
        query,
        response_a: responseA.content,
        response_b: responseB.content,
        strategy_a: strategyA,
        strategy_b: strategyB,
        latency_a_ms: responseA.latency_ms,
        latency_b_ms: responseB.latency_ms,
      })
      setComparisonResult(result)
    } catch (err) {
      console.error('Error comparing responses:', err)
    } finally {
      setIsComparing(false)
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

  const canRun = query.trim() && strategyA && strategyB && strategyA !== strategyB && sessionId
  const canCompare = responseA?.content && responseB?.content && !responseA.isLoading && !responseB.isLoading

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link
            to={`/${language}/system/strategies`}
            className="p-2 rounded-xl bg-surface-variant dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
          >
            <ArrowLeftIcon className="h-5 w-5 text-secondary" />
          </Link>
          <div>
            <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-200">
              {t('strategies.abTestTitle', 'Strategy A/B Test')}
            </h2>
            <p className="text-sm text-secondary dark:text-gray-400">
              {t('strategies.abTestSubtitle', 'Compare two strategies side by side')}
            </p>
          </div>
        </div>
      </div>

      {/* Strategy Selection */}
      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
          <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
            {t('strategies.strategyA', 'Strategy A')}
          </label>
          <select
            value={strategyA}
            onChange={(e) => setStrategyA(e.target.value)}
            className="w-full rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-3 text-primary-900 dark:text-gray-200 border-none focus:ring-2 focus:ring-primary"
          >
            {strategies.map((s) => (
              <option key={s.id} value={s.id} disabled={s.id === strategyB}>
                {s.name} {s.is_default && '(Default)'} {s.is_legacy && '(Legacy)'}
              </option>
            ))}
          </select>
        </div>

        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
          <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
            {t('strategies.strategyB', 'Strategy B')}
          </label>
          <select
            value={strategyB}
            onChange={(e) => setStrategyB(e.target.value)}
            className="w-full rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-3 text-primary-900 dark:text-gray-200 border-none focus:ring-2 focus:ring-primary"
          >
            {strategies.map((s) => (
              <option key={s.id} value={s.id} disabled={s.id === strategyA}>
                {s.name} {s.is_default && '(Default)'} {s.is_legacy && '(Legacy)'}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Query Input */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
        <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
          {t('strategies.testQuery', 'Test Query')}
        </label>
        <div className="flex gap-3">
          <textarea
            ref={queryInputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('strategies.queryPlaceholder', 'Enter a question to test both strategies...')}
            className="flex-1 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-3 text-primary-900 dark:text-gray-200 placeholder-gray-400 border-none focus:ring-2 focus:ring-primary resize-none"
            rows={3}
          />
          <button
            onClick={runTest}
            disabled={!canRun || responseA?.isLoading || responseB?.isLoading}
            className="flex items-center gap-2 rounded-xl bg-primary px-6 py-3 text-white font-medium transition-all hover:bg-primary-600 disabled:opacity-50 disabled:cursor-not-allowed self-end"
          >
            <PlayIcon className="h-5 w-5" />
            {t('strategies.runTest', 'Run Test')}
          </button>
        </div>
      </div>

      {/* Responses Side by Side */}
      {(responseA || responseB) && (
        <div className="grid grid-cols-2 gap-4">
          {/* Response A */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <SparklesIcon className="h-5 w-5 text-blue-500" />
                <span className="font-semibold text-primary-900 dark:text-gray-200">
                  {responseA?.strategy_name || strategyA}
                </span>
              </div>
              {responseA && !responseA.isLoading && (
                <span className="text-xs text-secondary dark:text-gray-400 flex items-center gap-1">
                  <ClockIcon className="h-4 w-4" />
                  {(responseA.latency_ms / 1000).toFixed(1)}s
                </span>
              )}
            </div>

            <div className="min-h-[200px] max-h-[400px] overflow-y-auto rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
              {responseA?.isLoading ? (
                <div className="flex items-center justify-center h-40">
                  <ArrowPathIcon className="h-6 w-6 animate-spin text-primary" />
                </div>
              ) : responseA?.error ? (
                <p className="text-red-500">{responseA.error}</p>
              ) : responseA?.content ? (
                <MarkdownRenderer content={responseA.content} />
              ) : (
                <p className="text-secondary dark:text-gray-400 text-center">
                  {t('strategies.awaitingResponse', 'Awaiting response...')}
                </p>
              )}
            </div>
          </div>

          {/* Response B */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <SparklesIcon className="h-5 w-5 text-purple-500" />
                <span className="font-semibold text-primary-900 dark:text-gray-200">
                  {responseB?.strategy_name || strategyB}
                </span>
              </div>
              {responseB && !responseB.isLoading && (
                <span className="text-xs text-secondary dark:text-gray-400 flex items-center gap-1">
                  <ClockIcon className="h-4 w-4" />
                  {(responseB.latency_ms / 1000).toFixed(1)}s
                </span>
              )}
            </div>

            <div className="min-h-[200px] max-h-[400px] overflow-y-auto rounded-xl bg-surface-variant dark:bg-gray-700 p-4">
              {responseB?.isLoading ? (
                <div className="flex items-center justify-center h-40">
                  <ArrowPathIcon className="h-6 w-6 animate-spin text-primary" />
                </div>
              ) : responseB?.error ? (
                <p className="text-red-500">{responseB.error}</p>
              ) : responseB?.content ? (
                <MarkdownRenderer content={responseB.content} />
              ) : (
                <p className="text-secondary dark:text-gray-400 text-center">
                  {t('strategies.awaitingResponse', 'Awaiting response...')}
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Compare Button */}
      {canCompare && !comparisonResult && (
        <div className="flex justify-center">
          <button
            onClick={compareResponses}
            disabled={isComparing}
            className="flex items-center gap-2 rounded-xl bg-gradient-to-r from-blue-500 to-purple-500 px-8 py-4 text-white font-medium transition-all hover:from-blue-600 hover:to-purple-600 disabled:opacity-50 disabled:cursor-not-allowed shadow-lg"
          >
            {isComparing ? (
              <>
                <ArrowPathIcon className="h-5 w-5 animate-spin" />
                {t('strategies.comparing', 'Analyzing with AI...')}
              </>
            ) : (
              <>
                <ScaleIcon className="h-5 w-5" />
                {t('strategies.compareResponses', 'Compare Responses with AI')}
              </>
            )}
          </button>
        </div>
      )}

      {/* Comparison Results */}
      {comparisonResult && (
        <div className="space-y-4">
          {/* Winner Banner */}
          <div className="rounded-2xl bg-gradient-to-r from-green-500 to-emerald-500 p-6 text-white shadow-lg">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm opacity-80">{t('strategies.overallWinner', 'Overall Winner')}</p>
                <p className="text-2xl font-bold">
                  {strategies.find((s) => s.id === comparisonResult.overall_winner)?.name || comparisonResult.overall_winner}
                </p>
              </div>
              <CheckCircleIcon className="h-12 w-12 opacity-80" />
            </div>
          </div>

          {/* Score Cards */}
          <div className="grid grid-cols-2 gap-4">
            <ResponseScoreCard
              scores={comparisonResult.scores_a}
              strategyName={strategies.find((s) => s.id === comparisonResult.strategy_a)?.name || comparisonResult.strategy_a}
              latencyMs={responseA?.latency_ms || 0}
              isWinner={comparisonResult.overall_winner === comparisonResult.strategy_a}
            />
            <ResponseScoreCard
              scores={comparisonResult.scores_b}
              strategyName={strategies.find((s) => s.id === comparisonResult.strategy_b)?.name || comparisonResult.strategy_b}
              latencyMs={responseB?.latency_ms || 0}
              isWinner={comparisonResult.overall_winner === comparisonResult.strategy_b}
            />
          </div>

          {/* Analysis */}
          <div className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1">
            <h3 className="font-semibold text-primary-900 dark:text-gray-200 mb-3 flex items-center gap-2">
              <ChartBarIcon className="h-5 w-5 text-primary" />
              {t('strategies.aiAnalysis', 'AI Analysis')}
            </h3>
            <p className="text-secondary dark:text-gray-400 whitespace-pre-wrap">
              {comparisonResult.analysis}
            </p>
          </div>

          {/* Recommendation */}
          <div className="rounded-2xl bg-blue-50 dark:bg-blue-900/20 p-5 border border-blue-200 dark:border-blue-800">
            <h3 className="font-semibold text-blue-700 dark:text-blue-300 mb-2">
              {t('strategies.recommendation', 'Recommendation')}
            </h3>
            <p className="text-blue-600 dark:text-blue-400">
              {comparisonResult.recommendation}
            </p>
          </div>

          {/* Run Again Button */}
          <div className="flex justify-center">
            <button
              onClick={() => {
                setComparisonResult(null)
                setResponseA(null)
                setResponseB(null)
                setQuery('')
                queryInputRef.current?.focus()
              }}
              className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-6 py-3 text-primary-700 dark:text-primary-300 font-medium transition-all hover:bg-gray-200 dark:hover:bg-gray-600"
            >
              <ArrowPathIcon className="h-5 w-5" />
              {t('strategies.runAnotherTest', 'Run Another Test')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

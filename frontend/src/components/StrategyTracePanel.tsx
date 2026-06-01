/**
 * StrategyTracePanel - Admin-only panel that visualises a Strategy OS execution trace.
 *
 * Renders:
 * - Header summary (active/legacy badge, spec name, routing reason, totals)
 * - Optional adaptive scoring table for candidate strategies
 * - DAG node waterfall (live nodes during streaming, full trace afterwards)
 * - Optional context budget bar with dropped items
 * - Footer with timing/token totals and a copyable trace id
 *
 * Designed to be visually consistent with FederatedAgentPanel.
 */

import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ChevronDownIcon,
  ChevronRightIcon,
  CpuChipIcon,
  ClockIcon,
  CheckCircleIcon,
  XCircleIcon,
  MinusCircleIcon,
  Squares2X2Icon,
  ScaleIcon,
} from '@heroicons/react/24/outline'
import {
  StrategyTraceResponse,
  NodeTraceEntry,
  ContextBudgetSummary,
  StrategyNodeEvent,
} from '../api/client'
import { CopyIconButton } from './CopyButton'

interface StrategyTracePanelProps {
  trace: StrategyTraceResponse
  liveNodes?: StrategyNodeEvent[]
}

type AnyNode = NodeTraceEntry | StrategyNodeEvent

function isFinalNode(node: AnyNode): node is NodeTraceEntry {
  return 'tokens_used' in node || 'llm_call_ids' in node
}

function statusStyles(status: string): { dot: string; text: string; label: string } {
  const s = (status || '').toLowerCase()
  if (s === 'success' || s === 'ok' || s === 'completed') {
    return {
      dot: 'bg-green-500',
      text: 'text-green-700 dark:text-green-300',
      label: status,
    }
  }
  if (s === 'failed' || s === 'error' || s === 'timed_out') {
    return {
      dot: 'bg-red-500',
      text: 'text-red-700 dark:text-red-300',
      label: status,
    }
  }
  if (s === 'skipped' || s === 'empty') {
    return {
      dot: 'bg-gray-400',
      text: 'text-gray-600 dark:text-gray-400',
      label: status,
    }
  }
  return {
    dot: 'bg-primary-400',
    text: 'text-primary-700 dark:text-primary-300',
    label: status || 'pending',
  }
}

function StatusIcon({ status }: { status: string }) {
  const s = (status || '').toLowerCase()
  if (s === 'success' || s === 'ok' || s === 'completed') {
    return <CheckCircleIcon className="h-3 w-3 text-green-500" />
  }
  if (s === 'failed' || s === 'error' || s === 'timed_out') {
    return <XCircleIcon className="h-3 w-3 text-red-500" />
  }
  if (s === 'skipped' || s === 'empty') {
    return <MinusCircleIcon className="h-3 w-3 text-gray-400" />
  }
  return <span className="h-2 w-2 rounded-full bg-primary-400 animate-pulse inline-block" />
}

function formatScoreCell(value: unknown): string {
  if (typeof value === 'number') return value.toFixed(3)
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  return String(value)
}

const SCORE_COLUMNS: Array<{ key: string; label: string }> = [
  { key: 'strategy_id', label: 'Strategy' },
  { key: 'latency_fit', label: 'Latency' },
  { key: 'quality', label: 'Quality' },
  { key: 'resource_fit', label: 'Resource' },
  { key: 'residency', label: 'Residency' },
  { key: 'fast_path_bias', label: 'FastPath' },
  { key: 'total', label: 'Total' },
]

export default function StrategyTracePanel({ trace, liveNodes }: StrategyTracePanelProps) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({
    scores: true,
    nodes: true,
    budget: true,
  })

  const toggleSection = (section: string) => {
    setExpandedSections(prev => ({ ...prev, [section]: !prev[section] }))
  }

  const isLive = Boolean(liveNodes && liveNodes.length > 0)

  // Pick which node list to render (live wins while streaming, otherwise final trace)
  const nodes: AnyNode[] = useMemo(() => {
    if (isLive && liveNodes) return liveNodes
    return trace.nodes_executed ?? []
  }, [isLive, liveNodes, trace.nodes_executed])

  const maxDuration = useMemo(() => {
    let max = 0
    for (const n of nodes) {
      const d = n.duration_ms ?? 0
      if (d > max) max = d
    }
    return max || 1
  }, [nodes])

  const adaptiveScores = trace.adaptive_scores ?? []
  const hasAdaptiveScores = adaptiveScores.length > 0
  const budget: ContextBudgetSummary | null = trace.context_budget ?? null

  // Determine the winning candidate (highest "total" score) for highlighting
  const winningStrategyId = useMemo(() => {
    if (!hasAdaptiveScores) return null
    let best: { id: string | null; score: number } = { id: null, score: -Infinity }
    for (const row of adaptiveScores) {
      const total = typeof row.total === 'number' ? (row.total as number) : -Infinity
      const id = typeof row.strategy_id === 'string' ? (row.strategy_id as string) : null
      if (id && total > best.score) best = { id, score: total }
    }
    return best.id
  }, [adaptiveScores, hasAdaptiveScores])

  const totalDurationMs = trace.total_duration_ms ?? 0
  const llmCallCount = trace.llm_call_count ?? 0
  const totalLlmTokens = trace.total_llm_tokens ?? 0

  const headerBadge = trace.active
    ? {
        label: t('strategyPanel.active', { defaultValue: 'Strategy OS' }),
        cls: 'bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-300',
      }
    : {
        label: t('strategyPanel.fallback', { defaultValue: 'Legacy Fallback' }),
        cls: 'bg-orange-100 dark:bg-orange-900/40 text-orange-700 dark:text-orange-300',
      }

  return (
    <div className="mt-2">
      {/* Header - always visible */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 text-xs text-secondary dark:text-gray-400 hover:text-primary dark:hover:text-primary-300 transition-colors"
      >
        {expanded ? (
          <ChevronDownIcon className="h-3 w-3" />
        ) : (
          <ChevronRightIcon className="h-3 w-3" />
        )}
        <Squares2X2Icon className="h-3 w-3" />
        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${headerBadge.cls}`}>
          {headerBadge.label}
        </span>
        <span className="font-medium truncate max-w-[160px]">
          {trace.spec_selected || '—'}
          {trace.spec_version ? (
            <span className="ml-1 text-[10px] opacity-60">v{trace.spec_version}</span>
          ) : null}
        </span>
        {trace.routing_reason ? (
          <span className="text-[10px] opacity-60 truncate max-w-[200px]">
            {trace.routing_reason}
          </span>
        ) : null}
        <span className="ml-auto text-[10px] opacity-70 flex items-center gap-2">
          <span>{totalDurationMs.toFixed(0)}ms</span>
          <span>
            {llmCallCount} {t('strategyPanel.llmCalls', { defaultValue: 'LLM calls' })}
          </span>
          <span>
            {totalLlmTokens.toLocaleString()}{' '}
            {t('strategyPanel.tokens', { defaultValue: 'tokens' })}
          </span>
        </span>
      </button>

      {expanded && (
        <div className="mt-2 space-y-3 pl-4 border-l-2 border-primary-200 dark:border-primary-800">
          {/* Fast-path / fallback notice */}
          {(trace.fallback_reason || trace.fast_path_eligible) && (
            <div className="flex flex-wrap items-center gap-2 text-[10px] text-secondary dark:text-gray-500">
              {trace.fast_path_eligible && (
                <span className="px-1.5 py-0.5 rounded bg-primary-50 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300">
                  {t('strategyPanel.fastPathEligible', {
                    defaultValue: 'Fast-path eligible',
                  })}
                </span>
              )}
              {trace.fallback_reason && (
                <span className="px-1.5 py-0.5 rounded bg-orange-50 dark:bg-orange-900/30 text-orange-700 dark:text-orange-300">
                  {t('strategyPanel.fallbackReason', { defaultValue: 'Fallback' })}:{' '}
                  {trace.fallback_reason}
                </span>
              )}
            </div>
          )}

          {/* Adaptive Scores */}
          {hasAdaptiveScores && (
            <div className="space-y-1">
              <button
                onClick={() => toggleSection('scores')}
                className="flex items-center gap-1 text-xs font-medium text-primary-700 dark:text-primary-300"
              >
                {expandedSections.scores ? (
                  <ChevronDownIcon className="h-3 w-3" />
                ) : (
                  <ChevronRightIcon className="h-3 w-3" />
                )}
                <ScaleIcon className="h-3 w-3" />
                {t('strategyPanel.adaptiveScores', { defaultValue: 'Adaptive scores' })} (
                {adaptiveScores.length})
              </button>

              {expandedSections.scores && (
                <div className="overflow-x-auto">
                  <table className="w-full text-[10px] border-collapse">
                    <thead>
                      <tr className="text-secondary dark:text-gray-500">
                        {SCORE_COLUMNS.map(col => (
                          <th
                            key={col.key}
                            className="text-left font-medium px-2 py-1 border-b border-gray-200 dark:border-gray-700"
                          >
                            {col.label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {adaptiveScores.map((row, idx) => {
                        const id =
                          typeof row.strategy_id === 'string'
                            ? (row.strategy_id as string)
                            : `#${idx}`
                        const isWinner = winningStrategyId && id === winningStrategyId
                        return (
                          <tr
                            key={`${id}-${idx}`}
                            className={
                              isWinner
                                ? 'bg-primary-50 dark:bg-primary-900/30 font-medium'
                                : 'hover:bg-gray-50 dark:hover:bg-gray-800/50'
                            }
                          >
                            {SCORE_COLUMNS.map(col => (
                              <td
                                key={col.key}
                                className="px-2 py-1 border-b border-gray-100 dark:border-gray-800 text-primary-900 dark:text-gray-200"
                              >
                                {col.key === 'strategy_id' && isWinner ? (
                                  <span className="inline-flex items-center gap-1">
                                    <CheckCircleIcon className="h-3 w-3 text-green-500" />
                                    {formatScoreCell(row[col.key])}
                                  </span>
                                ) : (
                                  formatScoreCell(row[col.key])
                                )}
                              </td>
                            ))}
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* DAG Node Waterfall */}
          <div className="space-y-1">
            <button
              onClick={() => toggleSection('nodes')}
              className="flex items-center gap-1 text-xs font-medium text-primary-700 dark:text-primary-300"
            >
              {expandedSections.nodes ? (
                <ChevronDownIcon className="h-3 w-3" />
              ) : (
                <ChevronRightIcon className="h-3 w-3" />
              )}
              <CpuChipIcon className="h-3 w-3" />
              {isLive
                ? t('strategyPanel.liveNodes', { defaultValue: 'Live nodes' })
                : t('strategyPanel.nodes', { defaultValue: 'Nodes' })}{' '}
              ({nodes.length})
              {isLive && (
                <span className="ml-1 inline-flex items-center gap-1 text-[10px] text-green-600 dark:text-green-400">
                  <span className="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse" />
                  {t('strategyPanel.streaming', { defaultValue: 'streaming' })}
                </span>
              )}
            </button>

            {expandedSections.nodes && (
              <div className="space-y-1 pl-4">
                {nodes.length === 0 ? (
                  <div className="text-[10px] text-secondary dark:text-gray-500 italic">
                    {t('strategyPanel.noNodes', { defaultValue: 'No nodes executed yet' })}
                  </div>
                ) : (
                  nodes.map((node, idx) => {
                    const isLast = idx === nodes.length - 1
                    const styles = statusStyles(node.status)
                    const duration = node.duration_ms ?? 0
                    const widthPct = Math.max(2, (duration / maxDuration) * 100)
                    const tokens = isFinalNode(node) ? node.tokens_used : 0
                    const error = isFinalNode(node) ? node.error : null

                    return (
                      <div
                        key={`${node.node_id}-${idx}`}
                        className={`p-2 rounded-lg bg-surface dark:bg-gray-800/50 text-xs space-y-1 ${
                          isLive && isLast ? 'ring-1 ring-primary-300 dark:ring-primary-700' : ''
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <span className={`h-2 w-2 rounded-full ${styles.dot} flex-shrink-0`} />
                          <span className="font-medium text-primary-900 dark:text-gray-100 truncate max-w-[160px]">
                            {node.node_type}
                          </span>
                          <span className="text-[10px] text-secondary dark:text-gray-500 truncate max-w-[140px]">
                            {node.node_id}
                          </span>
                          <span
                            className={`text-[10px] capitalize ${styles.text} ml-auto flex items-center gap-1`}
                          >
                            <StatusIcon status={node.status} />
                            {styles.label}
                          </span>
                        </div>

                        {/* Duration bar */}
                        <div className="flex items-center gap-2">
                          <div className="flex-1 h-1.5 rounded bg-gray-100 dark:bg-gray-800 overflow-hidden">
                            <div
                              className={`h-full ${styles.dot} ${
                                isLive && isLast ? 'animate-pulse' : ''
                              }`}
                              style={{ width: `${widthPct}%` }}
                            />
                          </div>
                          <span className="text-[10px] text-secondary dark:text-gray-500 flex items-center gap-1 flex-shrink-0">
                            <ClockIcon className="h-3 w-3" />
                            {duration.toFixed(0)}ms
                          </span>
                          {tokens > 0 && (
                            <span className="text-[10px] text-secondary dark:text-gray-500 flex-shrink-0">
                              {tokens.toLocaleString()}{' '}
                              {t('strategyPanel.tokensShort', { defaultValue: 'tok' })}
                            </span>
                          )}
                        </div>

                        {error && (
                          <p className="text-[10px] text-red-600 dark:text-red-300 line-clamp-2">
                            {error}
                          </p>
                        )}
                      </div>
                    )
                  })
                )}
              </div>
            )}
          </div>

          {/* Context Budget */}
          {budget && (
            <div className="space-y-1">
              <button
                onClick={() => toggleSection('budget')}
                className="flex items-center gap-1 text-xs font-medium text-primary-700 dark:text-primary-300"
              >
                {expandedSections.budget ? (
                  <ChevronDownIcon className="h-3 w-3" />
                ) : (
                  <ChevronRightIcon className="h-3 w-3" />
                )}
                {t('strategyPanel.contextBudget', { defaultValue: 'Context budget' })}
              </button>

              {expandedSections.budget && (
                <div className="pl-4 space-y-1.5">
                  <div className="h-2 rounded bg-gray-100 dark:bg-gray-800 overflow-hidden">
                    <div
                      className="h-full bg-primary-500 dark:bg-primary-400"
                      style={{
                        width: `${
                          budget.total_budget_tokens > 0
                            ? Math.min(
                                100,
                                (budget.tokens_used / budget.total_budget_tokens) * 100,
                              )
                            : 0
                        }%`,
                      }}
                    />
                  </div>
                  <div className="text-[10px] text-secondary dark:text-gray-400">
                    {t('strategyPanel.budgetUsed', {
                      defaultValue: 'Used {{used}} / {{total}} tokens ({{dropped}} dropped)',
                      used: budget.tokens_used.toLocaleString(),
                      total: budget.total_budget_tokens.toLocaleString(),
                      dropped: budget.dropped_count,
                    })}
                    <span className="ml-2 opacity-70">
                      {budget.included_count}{' '}
                      {t('strategyPanel.included', { defaultValue: 'included' })}
                    </span>
                  </div>

                  {budget.dropped_items && budget.dropped_items.length > 0 && (
                    <ul className="space-y-0.5">
                      {budget.dropped_items.slice(0, 6).map((item, idx) => (
                        <li
                          key={idx}
                          className="text-[10px] text-gray-600 dark:text-gray-400 flex items-center gap-2"
                        >
                          <span className="px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-800 font-mono">
                            {item.kind}
                          </span>
                          <span className="text-secondary dark:text-gray-500">
                            {item.tokens.toLocaleString()}{' '}
                            {t('strategyPanel.tokensShort', { defaultValue: 'tok' })}
                          </span>
                          <span className="opacity-70 truncate">{item.reason}</span>
                        </li>
                      ))}
                      {budget.dropped_items.length > 6 && (
                        <li className="text-[10px] text-secondary dark:text-gray-500">
                          {t('strategyPanel.moreDropped', {
                            defaultValue: '+{{count}} more dropped items',
                            count: budget.dropped_items.length - 6,
                          })}
                        </li>
                      )}
                    </ul>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Timing footer */}
          <div className="flex flex-wrap items-center gap-3 text-[10px] text-secondary dark:text-gray-500 pt-2 border-t border-gray-200 dark:border-gray-700">
            <span>{totalDurationMs.toFixed(0)}ms</span>
            <span>
              {llmCallCount} {t('strategyPanel.llmCalls', { defaultValue: 'LLM calls' })}
            </span>
            <span>
              {totalLlmTokens.toLocaleString()}{' '}
              {t('strategyPanel.tokens', { defaultValue: 'tokens' })}
            </span>
            {trace.trace_id && (
              <div className="ml-auto flex items-center gap-1">
                <span className="font-mono text-[10px] text-gray-500 dark:text-gray-400 truncate max-w-[180px]">
                  {trace.trace_id}
                </span>
                <CopyIconButton text={trace.trace_id} size="xs" />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * SimplifiedAgentPanel - User-friendly summary of the full agent trace.
 *
 * Replaces FederatedAgentPanel for non-technical users. Shows:
 * - Collapsible "Research Summary" header with document count and time
 * - List of searches performed (in plain language)
 * - Sources found with human-readable relevance labels
 *
 * Hides orchestrator steps, worker execution details, token counts, costs,
 * raw JSON inputs, and internal reasoning.
 *
 * Filters:
 * - Removes zero-result search steps (user doesn't have that data source)
 * - Hides internal processing tasks (refine_query, summarize)
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ChevronDownIcon,
  ChevronRightIcon,
  MagnifyingGlassIcon,
  DocumentTextIcon,
  GlobeAltIcon,
  CheckCircleIcon,
  XCircleIcon,
} from '@heroicons/react/24/outline'
import { FederatedAgentTrace } from '../api/client'

interface SimplifiedAgentPanelProps {
  trace: FederatedAgentTrace
}

/** Internal task types that normal users should never see. */
const INTERNAL_TASK_TYPES = new Set(['refine_query', 'summarize'])

/**
 * Map a raw similarity score (0-1) to a human-readable relevance label.
 */
function getRelevanceLabel(score: number, t: (key: string) => string): { label: string; className: string } {
  if (score >= 0.85) {
    return { label: t('userView.relevanceHigh'), className: 'text-green-600 dark:text-green-400' }
  }
  if (score >= 0.65) {
    return { label: t('userView.relevanceGood'), className: 'text-primary-600 dark:text-primary-400' }
  }
  return { label: t('userView.relevancePartial'), className: 'text-secondary dark:text-gray-400' }
}

export default function SimplifiedAgentPanel({ trace }: SimplifiedAgentPanelProps) {
  const [expanded, setExpanded] = useState(false)
  const [docsVisible, setDocsVisible] = useState(5)
  const { t } = useTranslation()

  const totalDocs = trace.sources?.documents?.length ?? 0
  const totalLinks = trace.sources?.web_links?.length ?? 0
  const totalMs = trace.timing?.total_ms ?? 0
  const workerSteps = trace.worker_steps ?? []

  // Build search descriptions from worker steps:
  // - Skip internal processing tasks (refine_query, summarize)
  // - Skip search steps with zero results (user doesn't have that data source)
  const searches = workerSteps
    .filter((step) => {
      if (INTERNAL_TASK_TYPES.has(step.task_type)) return false
      const docCount = (step.documents ?? []).length
      const linkCount = (step.web_links ?? []).length
      if (docCount + linkCount === 0) return false
      return true
    })
    .map((step) => {
      const docCount = (step.documents ?? []).length
      const linkCount = (step.web_links ?? []).length
      return {
        label: t(`userView.searchType.${step.task_type}`, { defaultValue: step.task_type }),
        resultCount: docCount + linkCount,
        success: step.success,
      }
    })

  const allDocuments = trace.sources?.documents ?? []
  const remainingDocs = totalDocs - docsVisible

  return (
    <div className="mt-2">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-xs text-secondary dark:text-gray-400 hover:text-primary dark:hover:text-primary-300 transition-colors"
      >
        {expanded ? (
          <ChevronDownIcon className="h-3 w-3" />
        ) : (
          <ChevronRightIcon className="h-3 w-3" />
        )}
        <MagnifyingGlassIcon className="h-3 w-3" />
        <span className="font-medium">{t('userView.researchSummary')}</span>
        <span className="text-[10px] opacity-60">
          {t('userView.docsFound', { count: totalDocs + totalLinks })}
          {' | '}
          {(totalMs / 1000).toFixed(1)}s
        </span>
      </button>

      {expanded && (
        <div className="mt-2 space-y-3 pl-4 border-l-2 border-primary-200 dark:border-primary-800">
          {/* Searches performed */}
          {searches.length > 0 && (
            <div className="space-y-1">
              <span className="text-[10px] font-medium text-secondary dark:text-gray-500">
                {t('userView.searchesPerformed')}
              </span>
              {searches.map((search, idx) => (
                <div
                  key={idx}
                  className="flex items-center gap-2 text-xs text-primary-800 dark:text-gray-300"
                >
                  {search.success ? (
                    <CheckCircleIcon className="h-3 w-3 text-green-500 flex-shrink-0" />
                  ) : (
                    <XCircleIcon className="h-3 w-3 text-red-500 flex-shrink-0" />
                  )}
                  <span>{search.label}</span>
                  <span className="text-[10px] text-secondary dark:text-gray-500">
                    &mdash; {t('userView.foundResults', { count: search.resultCount })}
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* Sources found - documents */}
          {totalDocs > 0 && (
            <div className="space-y-1">
              <span className="text-[10px] font-medium text-secondary dark:text-gray-500">
                {t('userView.sourcesFound')} ({totalDocs})
              </span>
              {allDocuments.slice(0, docsVisible).map((doc, idx) => {
                const rel = getRelevanceLabel(doc.score ?? 0, t)
                return (
                  <div
                    key={idx}
                    className="p-2 rounded bg-gray-50 dark:bg-gray-800 text-xs"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <DocumentTextIcon className="h-3 w-3 text-primary-500 flex-shrink-0" />
                        <span className="font-medium text-primary-700 dark:text-primary-300 truncate">
                          {doc.title}
                        </span>
                      </div>
                      <span className={`text-[10px] flex-shrink-0 ml-2 ${rel.className}`}>
                        {rel.label}
                      </span>
                    </div>
                    {doc.excerpt && (
                      <p className="text-[10px] text-gray-600 dark:text-gray-400 line-clamp-2 mt-1 pl-4">
                        {doc.excerpt}
                      </p>
                    )}
                  </div>
                )
              })}
              {remainingDocs > 0 && (
                <button
                  onClick={() => setDocsVisible((prev) => prev + 10)}
                  className="text-[10px] text-primary-600 dark:text-primary-400 hover:text-primary-700 dark:hover:text-primary-300 hover:underline cursor-pointer transition-colors"
                >
                  +{remainingDocs} {t('userView.showMore', { count: remainingDocs })}
                </button>
              )}
            </div>
          )}

          {/* Web links */}
          {totalLinks > 0 && (
            <div className="space-y-1">
              <span className="text-[10px] font-medium text-secondary dark:text-gray-500">
                {t('agentPanel.webLinks')} ({totalLinks})
              </span>
              {(trace.sources?.web_links ?? []).slice(0, 3).map((link, idx) => (
                <div
                  key={idx}
                  className="flex items-center gap-1.5 text-xs"
                >
                  <GlobeAltIcon className="h-3 w-3 text-primary-500 flex-shrink-0" />
                  <a
                    href={link.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary dark:text-primary-400 hover:underline truncate"
                  >
                    {link.title || link.url}
                  </a>
                </div>
              ))}
              {totalLinks > 3 && (
                <span className="text-[10px] text-secondary dark:text-gray-500">
                  {t('agentPanel.moreLinks', { count: totalLinks - 3 })}
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

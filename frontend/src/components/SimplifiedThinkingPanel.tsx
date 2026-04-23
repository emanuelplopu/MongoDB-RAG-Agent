/**
 * SimplifiedThinkingPanel - User-friendly summary of search/tool operations.
 *
 * Replaces the inline thinking panel (search operations + tool calls) for
 * non-technical users. Shows simple query/result count list and plain-language
 * tool descriptions. Hides index types, raw durations, score percentages,
 * JSON inputs, and other developer-oriented details.
 */

import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ChevronDownIcon,
  ChevronRightIcon,
  MagnifyingGlassIcon,
  CheckCircleIcon,
  XCircleIcon,
} from '@heroicons/react/24/outline'
import { AgentThinking } from '../api/client'

interface SimplifiedThinkingPanelProps {
  thinking: AgentThinking
}

export default function SimplifiedThinkingPanel({ thinking }: SimplifiedThinkingPanelProps) {
  const [expanded, setExpanded] = useState(false)
  const { t } = useTranslation()

  const searchOps = thinking.search?.operations ?? []
  const toolCalls = thinking.tool_calls ?? []
  const totalResults = thinking.search?.total_results ?? 0

  // Nothing to show
  if (searchOps.length === 0 && toolCalls.length === 0) return null

  return (
    <div className="mt-2">
      {/* Collapsed header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-xs text-secondary dark:text-gray-400 hover:text-primary dark:hover:text-primary-300 transition-colors"
      >
        {expanded ? (
          <ChevronDownIcon className="h-3 w-3" />
        ) : (
          <ChevronRightIcon className="h-3 w-3" />
        )}
        <MagnifyingGlassIcon className="h-3 w-3" />
        <span>{t('userView.searchDetails')}</span>
        {totalResults > 0 && (
          <span className="text-[10px] opacity-60">
            ({t('userView.foundResults', { count: totalResults })})
          </span>
        )}
      </button>

      {expanded && (
        <div className="mt-2 space-y-2 pl-4 border-l-2 border-primary-200 dark:border-primary-800">
          {/* Search operations - simplified */}
          {searchOps.map((op, idx) => (
            <div
              key={`search-${idx}`}
              className="text-xs text-primary-800 dark:text-gray-300 space-y-0.5"
            >
              <div className="flex items-center gap-2">
                <CheckCircleIcon className="h-3 w-3 text-green-500 flex-shrink-0" />
                <span className="text-secondary dark:text-gray-400">{t('userView.searched')}:</span>
                <span className="truncate max-w-[250px]">
                  {op.query.length > 80 ? op.query.substring(0, 80) + '...' : op.query}
                </span>
              </div>
              <p className="text-[10px] text-secondary dark:text-gray-500 pl-5">
                {op.results_count > 0
                  ? t('userView.foundResults', { count: op.results_count })
                  : t('userView.noResults')}
              </p>
            </div>
          ))}

          {/* Tool calls - simplified */}
          {toolCalls.map((tool, idx) => (
            <div
              key={`tool-${idx}`}
              className="flex items-center gap-2 text-xs text-primary-800 dark:text-gray-300"
            >
              {tool.success ? (
                <CheckCircleIcon className="h-3 w-3 text-green-500 flex-shrink-0" />
              ) : (
                <XCircleIcon className="h-3 w-3 text-red-500 flex-shrink-0" />
              )}
              <span>
                {t(`userView.toolType.${tool.tool_name}`, { defaultValue: t('userView.toolType.default') })}
              </span>
              <span className="text-[10px] text-secondary dark:text-gray-500">
                &mdash; {tool.success ? t('userView.toolSuccess') : t('userView.toolFailed')}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

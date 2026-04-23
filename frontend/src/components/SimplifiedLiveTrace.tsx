/**
 * SimplifiedLiveTrace - User-friendly live progress display during agent streaming.
 *
 * Shows human-readable phase labels, elapsed time in seconds, document count,
 * and a stop button. Hides all technical details (tokens, orchestrator/worker
 * split, raw milliseconds, step reasoning).
 */

import { useTranslation } from 'react-i18next'
import { StopIcon } from '@heroicons/react/24/outline'

interface SimplifiedLiveTraceProps {
  liveTrace: {
    orchestrator_steps: Array<{ phase: string; reasoning: string; output: string; duration_ms: number; tokens: number }>
    worker_steps: Array<{ task_id: string; task_type: string; tool: string; duration_ms: number; success: boolean; documents: Array<{ title: string; score: number; excerpt: string }> }>
    stats: { total_tokens: number; orchestrator_tokens: number; worker_tokens: number; cost_usd: number }
    startTime: number
    currentPhase: string
  }
  elapsedTime: number
  onStop: () => void
}

export default function SimplifiedLiveTrace({ liveTrace, elapsedTime, onStop }: SimplifiedLiveTraceProps) {
  const { t } = useTranslation()

  // Count total documents found across all worker steps
  const totalDocsFound = liveTrace.worker_steps.reduce(
    (sum, step) => sum + (step.documents?.length ?? 0),
    0
  )

  return (
    <div className="bg-surface dark:bg-gray-700/50 rounded-xl p-4 space-y-3">
      {/* Phase label and elapsed time */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {/* Animated spinner */}
          <div className="relative h-5 w-5 flex-shrink-0">
            <div className="absolute inset-0 rounded-full border-2 border-primary/20" />
            <div className="absolute inset-0 rounded-full border-2 border-primary border-t-transparent animate-spin" />
          </div>
          <span className="text-sm font-medium text-primary-800 dark:text-primary-200">
            {t(`userView.phases.${liveTrace.currentPhase}`, { defaultValue: t('userView.phases.starting') })}
          </span>
        </div>
        <span className="text-xs text-secondary dark:text-gray-400">
          {t('userView.seconds', { count: elapsedTime })}
        </span>
      </div>

      {/* Documents found so far */}
      {totalDocsFound > 0 && (
        <p className="text-xs text-secondary dark:text-gray-400 pl-8">
          {t('userView.docsFoundSoFar', { count: totalDocsFound })}
        </p>
      )}

      {/* Stop button */}
      <div className="flex justify-end pt-1">
        <button
          onClick={onStop}
          className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-900/50 transition-colors text-xs"
        >
          <StopIcon className="h-3 w-3" />
          <span>{t('chatPage.stop')}</span>
        </button>
      </div>
    </div>
  )
}

/**
 * SimplifiedLiveTrace - User-friendly step-by-step live progress display.
 *
 * Shows progressive updates as the agent thinks and searches:
 * - "Understanding your question..." with intent summary (from analyze step)
 * - "Planning search strategy..." with planned searches (from plan step)
 * - "Searching..." with per-task results as they complete (from worker steps)
 * - "Writing your answer..." at the end (synthesize step)
 *
 * Hides all technical details (tokens, orchestrator/worker split, raw
 * milliseconds, step reasoning internals).
 */

import { useTranslation } from 'react-i18next'
import {
  StopIcon,
  CheckCircleIcon,
  MagnifyingGlassIcon,
} from '@heroicons/react/24/outline'

interface OrchestratorStep {
  phase: string
  reasoning: string
  output: string
  duration_ms: number
  tokens: number
  tasks?: Array<{ id: string; type: string; query: string }>
}

interface WorkerStep {
  task_id: string
  task_type: string
  tool: string
  duration_ms: number
  success: boolean
  documents: Array<{ title: string; score: number; excerpt: string }>
}

interface SimplifiedLiveTraceProps {
  liveTrace: {
    orchestrator_steps: OrchestratorStep[]
    worker_steps: WorkerStep[]
    stats: { total_tokens: number; orchestrator_tokens: number; worker_tokens: number; cost_usd: number }
    startTime: number
    currentPhase: string
  }
  elapsedTime: number
  onStop: () => void
}

/** Internal task types never shown to normal users. */
const INTERNAL_TASK_TYPES = new Set(['refine_query', 'summarize'])

/** Truncate long reasoning text for display. */
function truncate(text: string, max = 120): string {
  if (!text || text.length <= max) return text
  return text.slice(0, max).trimEnd() + '…'
}

export default function SimplifiedLiveTrace({ liveTrace, elapsedTime, onStop }: SimplifiedLiveTraceProps) {
  const { t } = useTranslation()

  // Extract phase-specific orchestrator steps
  const analyzeStep = liveTrace.orchestrator_steps.find((s) => s.phase === 'analyze')
  const planStep = liveTrace.orchestrator_steps.find((s) => s.phase === 'plan')

  // Filter out internal task types from planned tasks and worker steps
  const plannedTasks = (planStep?.tasks ?? []).filter((task) => !INTERNAL_TASK_TYPES.has(task.type))
  const visibleWorkerSteps = liveTrace.worker_steps.filter((step) => !INTERNAL_TASK_TYPES.has(step.task_type))

  // Track which planned tasks have completed by matching task_type
  const completedTypes = new Set(visibleWorkerSteps.map((s) => s.task_type))

  // Total documents found across all worker steps
  const totalDocsFound = visibleWorkerSteps.reduce(
    (sum, step) => sum + (step.documents?.length ?? 0),
    0
  )

  const currentPhase = liveTrace.currentPhase
  const isExecuting = currentPhase === 'executing'
  const isSynthesize = currentPhase === 'synthesize'
  const isEvaluate = currentPhase === 'evaluate'

  return (
    <div className="bg-surface dark:bg-gray-700/50 rounded-xl p-4 space-y-3">
      {/* Current phase + elapsed time */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="relative h-5 w-5 flex-shrink-0">
            <div className="absolute inset-0 rounded-full border-2 border-primary/20" />
            <div className="absolute inset-0 rounded-full border-2 border-primary border-t-transparent animate-spin" />
          </div>
          <span className="text-sm font-medium text-primary-800 dark:text-primary-200">
            {t(`userView.phases.${currentPhase}`, { defaultValue: t('userView.phases.starting') })}
          </span>
        </div>
        <span className="text-xs text-secondary dark:text-gray-400">
          {t('userView.seconds', { count: elapsedTime })}
        </span>
      </div>

      {/* Step-by-step progress */}
      <div className="pl-8 space-y-2">
        {/* Analyze step - intent summary */}
        {analyzeStep && analyzeStep.reasoning && (
          <div className="flex items-start gap-2 text-xs">
            <CheckCircleIcon className="h-3.5 w-3.5 text-green-500 flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <span className="text-secondary dark:text-gray-400 font-medium">
                {t('userView.liveTrace.understood')}:
              </span>{' '}
              <span className="text-primary-800 dark:text-gray-300 italic">
                {truncate(analyzeStep.reasoning, 120)}
              </span>
            </div>
          </div>
        )}

        {/* Plan step - planned searches */}
        {plannedTasks.length > 0 && (
          <div className="flex items-start gap-2 text-xs">
            <CheckCircleIcon className="h-3.5 w-3.5 text-green-500 flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <span className="text-secondary dark:text-gray-400 font-medium">
                {t('userView.liveTrace.willSearch')}:
              </span>{' '}
              <span className="text-primary-800 dark:text-gray-300">
                {plannedTasks
                  .map((task) => t(`userView.searchType.${task.type}`, { defaultValue: task.type }))
                  .join(', ')}
              </span>
            </div>
          </div>
        )}

        {/* Worker step results - one line per completed search */}
        {visibleWorkerSteps.map((step, idx) => {
          const docCount = step.documents?.length ?? 0
          const label = t(`userView.searchType.${step.task_type}`, { defaultValue: step.task_type })
          return (
            <div key={`worker-${idx}`} className="flex items-start gap-2 text-xs">
              <CheckCircleIcon className="h-3.5 w-3.5 text-green-500 flex-shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <span className="text-primary-800 dark:text-gray-300 font-medium">{label}</span>
                <span className="text-secondary dark:text-gray-400">
                  {' — '}
                  {t('userView.foundResults', { count: docCount })}
                </span>
              </div>
            </div>
          )
        })}

        {/* In-progress placeholders for planned tasks still pending */}
        {isExecuting &&
          plannedTasks
            .filter((task) => !completedTypes.has(task.type))
            .map((task, idx) => {
              const label = t(`userView.searchType.${task.type}`, { defaultValue: task.type })
              return (
                <div key={`pending-${idx}`} className="flex items-start gap-2 text-xs">
                  <div className="relative h-3.5 w-3.5 flex-shrink-0 mt-0.5">
                    <div className="absolute inset-0 rounded-full border-2 border-primary/20" />
                    <div className="absolute inset-0 rounded-full border-2 border-primary border-t-transparent animate-spin" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <span className="text-primary-800 dark:text-gray-300 font-medium">{label}</span>
                    <span className="text-secondary dark:text-gray-400">
                      {' — '}
                      {t('userView.liveTrace.searchInProgress')}
                    </span>
                  </div>
                </div>
              )
            })}

        {/* Summary line during synthesize/evaluate */}
        {(isSynthesize || isEvaluate) && visibleWorkerSteps.length > 0 && (
          <div className="flex items-start gap-2 text-xs pt-1 border-t border-gray-200 dark:border-gray-600">
            <MagnifyingGlassIcon className="h-3.5 w-3.5 text-primary flex-shrink-0 mt-0.5" />
            <span className="text-secondary dark:text-gray-400">
              {t('userView.liveTrace.totalFound', {
                count: totalDocsFound,
                searches: visibleWorkerSteps.length,
              })}
            </span>
          </div>
        )}
      </div>

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

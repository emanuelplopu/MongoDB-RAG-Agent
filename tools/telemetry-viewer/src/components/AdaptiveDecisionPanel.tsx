import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { AdaptiveDecisionRecord } from '../types/strategy'

interface AdaptiveDecisionPanelProps {
  decision: AdaptiveDecisionRecord | null
  loading?: boolean
}

function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toFixed(3)
}

export default function AdaptiveDecisionPanel({
  decision,
  loading = false,
}: AdaptiveDecisionPanelProps) {
  const { t } = useTranslation()

  // Collect all score keys across candidates so the table is consistent.
  const scoreKeys = useMemo(() => {
    if (!decision?.candidates) return []
    const keys = new Set<string>()
    for (const c of decision.candidates) {
      Object.keys(c.scores || {}).forEach(k => keys.add(k))
    }
    return Array.from(keys)
  }, [decision])

  if (loading) {
    return (
      <div className="space-y-2">
        <div className="h-6 rounded bg-surface border border-gray-700/50 animate-pulse" />
        <div className="h-24 rounded bg-surface border border-gray-700/50 animate-pulse" />
        <div className="text-center text-[11px] text-gray-500">
          <span className="animate-spin inline-block mr-1">⏳</span>
          {t('strategy.adaptive.loading', { defaultValue: 'Loading adaptive decision…' })}
        </div>
      </div>
    )
  }

  if (!decision) {
    return (
      <div className="text-[11px] text-gray-500 italic py-4 text-center">
        {t('strategy.adaptive.empty', {
          defaultValue: 'No adaptive decision data for this run',
        })}
      </div>
    )
  }

  const candidates = decision.candidates || []

  return (
    <div className="space-y-3">
      {/* Header: selection + fast-path */}
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="text-gray-500">
          {t('strategy.adaptive.selected', { defaultValue: 'Selected' })}:
        </span>
        <span className="font-mono text-accent-green bg-accent-green/10 border border-accent-green/30 px-2 py-0.5 rounded">
          {decision.selected_strategy_id}
        </span>
        {decision.fast_path_eligible && (
          <span className="text-[10px] uppercase tracking-wide px-2 py-0.5 rounded border bg-accent-blue/20 text-accent-blue border-accent-blue/30">
            {t('strategy.adaptive.fastPath', { defaultValue: 'fast-path' })}
          </span>
        )}
        {decision.reason && (
          <span className="text-gray-400 italic">— {decision.reason}</span>
        )}
      </div>

      {/* Candidates table */}
      {candidates.length === 0 ? (
        <div className="text-[11px] text-gray-500 italic py-3 text-center">
          {t('strategy.adaptive.noCandidates', { defaultValue: 'No candidates evaluated' })}
        </div>
      ) : (
        <div className="overflow-x-auto border border-gray-700/50 rounded-md">
          <table className="w-full text-[11px]">
            <thead className="bg-surface text-gray-400 uppercase tracking-wide">
              <tr>
                <th className="text-left px-2 py-1.5 font-medium">
                  {t('strategy.adaptive.col.strategy', { defaultValue: 'strategy_id' })}
                </th>
                {scoreKeys.map(key => (
                  <th key={key} className="text-right px-2 py-1.5 font-medium font-mono">
                    {key}
                  </th>
                ))}
                <th className="text-right px-2 py-1.5 font-medium">
                  {t('strategy.adaptive.col.total', { defaultValue: 'total' })}
                </th>
              </tr>
            </thead>
            <tbody>
              {candidates.map(candidate => {
                const isWinner =
                  !candidate.disqualified &&
                  candidate.strategy_id === decision.selected_strategy_id
                const isDisqualified = !!candidate.disqualified
                const rowCls = isWinner
                  ? 'border-l-2 border-accent-green bg-accent-green/5'
                  : isDisqualified
                  ? 'opacity-60'
                  : ''
                const textCls = isDisqualified
                  ? 'line-through text-gray-500'
                  : 'text-gray-200'

                return (
                  <tr
                    key={candidate.strategy_id}
                    className={`border-t border-gray-700/50 ${rowCls}`}
                  >
                    <td className={`px-2 py-1.5 font-mono ${textCls}`}>
                      <div className="flex items-center gap-2">
                        <span className="break-all">{candidate.strategy_id}</span>
                        {isWinner && (
                          <span className="text-[9px] uppercase tracking-wide text-accent-green">
                            ✓ {t('strategy.adaptive.winner', { defaultValue: 'winner' })}
                          </span>
                        )}
                      </div>
                      {isDisqualified && candidate.disqualify_reason && (
                        <div
                          className="text-[10px] text-red-400 mt-0.5 normal-case no-underline"
                          title={candidate.disqualify_reason}
                        >
                          ⚠ {candidate.disqualify_reason}
                        </div>
                      )}
                    </td>
                    {scoreKeys.map(key => (
                      <td
                        key={key}
                        className={`px-2 py-1.5 text-right font-mono ${textCls}`}
                      >
                        {formatScore(candidate.scores?.[key])}
                      </td>
                    ))}
                    <td
                      className={`px-2 py-1.5 text-right font-mono font-semibold ${
                        isDisqualified
                          ? 'line-through text-gray-500'
                          : isWinner
                          ? 'text-accent-green'
                          : 'text-gray-100'
                      }`}
                    >
                      {formatScore(candidate.total)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Decision footer */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-gray-500">
        <span>
          decision_id: <span className="font-mono text-gray-400">{decision.decision_id}</span>
        </span>
        <span>
          capability_id:{' '}
          <span className="font-mono text-gray-400">{decision.capability_id}</span>
        </span>
        <span>
          decided_at:{' '}
          <span className="text-gray-400">
            {decision.decided_at ? new Date(decision.decided_at).toLocaleString() : '—'}
          </span>
        </span>
      </div>
    </div>
  )
}

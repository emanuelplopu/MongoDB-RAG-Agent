import { useTranslation } from 'react-i18next'

interface ContextBudgetData {
  total_budget_tokens: number
  tokens_used: number
  included_count: number
  dropped_count: number
  dropped_items: Array<{ kind: string; tokens: number; reason: string }>
}

interface ContextBudgetPanelProps {
  budget: ContextBudgetData | null
  loading?: boolean
}

function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return n.toLocaleString()
}

export default function ContextBudgetPanel({
  budget,
  loading = false,
}: ContextBudgetPanelProps) {
  const { t } = useTranslation()

  if (loading) {
    return (
      <div className="space-y-2">
        <div className="h-4 rounded bg-surface border border-gray-700/50 animate-pulse" />
        <div className="h-16 rounded bg-surface border border-gray-700/50 animate-pulse" />
        <div className="text-center text-[11px] text-gray-500">
          <span className="animate-spin inline-block mr-1">⏳</span>
          {t('strategy.budget.loading', { defaultValue: 'Loading context budget…' })}
        </div>
      </div>
    )
  }

  if (!budget) {
    return (
      <div className="text-[11px] text-gray-500 italic py-4 text-center">
        {t('strategy.budget.empty', {
          defaultValue: 'No context budget data for this run',
        })}
      </div>
    )
  }

  const total = Math.max(budget.total_budget_tokens || 0, 0)
  const used = Math.max(budget.tokens_used || 0, 0)
  const droppedTokens = (budget.dropped_items || []).reduce(
    (sum, item) => sum + (item.tokens || 0),
    0,
  )

  // Compute proportions, capped at 100% combined.
  const denominator = Math.max(total, used + droppedTokens, 1)
  const usedPct = Math.min((used / denominator) * 100, 100)
  const droppedPct = Math.min((droppedTokens / denominator) * 100, 100 - usedPct)
  const remainingPct = Math.max(100 - usedPct - droppedPct, 0)

  const remainingTokens = Math.max(total - used, 0)
  const utilization = total > 0 ? (used / total) * 100 : 0

  return (
    <div className="space-y-3">
      {/* Stacked bar */}
      <div className="space-y-1.5">
        <div className="flex h-4 w-full rounded-md overflow-hidden bg-surface border border-gray-700/50">
          {usedPct > 0 && (
            <div
              className="bg-accent-green h-full"
              style={{ width: `${usedPct}%` }}
              title={`${t('strategy.budget.used', { defaultValue: 'Used' })}: ${formatNumber(used)}`}
            />
          )}
          {droppedPct > 0 && (
            <div
              className="bg-red-500 h-full"
              style={{ width: `${droppedPct}%` }}
              title={`${t('strategy.budget.dropped', { defaultValue: 'Dropped' })}: ${formatNumber(droppedTokens)}`}
            />
          )}
          {remainingPct > 0 && (
            <div
              className="bg-gray-600 h-full"
              style={{ width: `${remainingPct}%` }}
              title={`${t('strategy.budget.remaining', { defaultValue: 'Remaining' })}: ${formatNumber(remainingTokens)}`}
            />
          )}
        </div>

        {/* Legend */}
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-gray-400">
          <span className="flex items-center gap-1">
            <span className="w-2.5 h-2.5 rounded-sm bg-accent-green" />
            {t('strategy.budget.used', { defaultValue: 'Used' })} ({formatNumber(used)})
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2.5 h-2.5 rounded-sm bg-red-500" />
            {t('strategy.budget.dropped', { defaultValue: 'Dropped' })} (
            {formatNumber(droppedTokens)})
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2.5 h-2.5 rounded-sm bg-gray-600" />
            {t('strategy.budget.remaining', { defaultValue: 'Remaining' })} (
            {formatNumber(remainingTokens)})
          </span>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-[11px]">
        <div className="bg-surface rounded-md px-2 py-1.5 border border-gray-700/50">
          <div className="text-[10px] text-gray-400 uppercase tracking-wide">
            {t('strategy.budget.tokens', { defaultValue: 'Tokens' })}
          </div>
          <div className="font-mono text-gray-100">
            {formatNumber(used)} / {formatNumber(total)}
            <span className="text-[10px] text-gray-400 ml-1">
              ({utilization.toFixed(1)}%)
            </span>
          </div>
        </div>
        <div className="bg-surface rounded-md px-2 py-1.5 border border-gray-700/50">
          <div className="text-[10px] text-gray-400 uppercase tracking-wide">
            {t('strategy.budget.included', { defaultValue: 'Included' })}
          </div>
          <div className="font-mono text-accent-green">
            {formatNumber(budget.included_count)}
          </div>
        </div>
        <div className="bg-surface rounded-md px-2 py-1.5 border border-gray-700/50">
          <div className="text-[10px] text-gray-400 uppercase tracking-wide">
            {t('strategy.budget.droppedItems', { defaultValue: 'Dropped' })}
          </div>
          <div
            className={`font-mono ${
              budget.dropped_count > 0 ? 'text-red-400' : 'text-gray-300'
            }`}
          >
            {formatNumber(budget.dropped_count)}
          </div>
        </div>
      </div>

      {/* Dropped items list */}
      {budget.dropped_items && budget.dropped_items.length > 0 && (
        <div className="space-y-1">
          <div className="text-[10px] uppercase tracking-wide text-gray-400">
            {t('strategy.budget.droppedList', { defaultValue: 'Dropped items' })}
          </div>
          <div className="border border-gray-700/50 rounded-md overflow-hidden">
            <table className="w-full text-[11px]">
              <thead className="bg-surface text-gray-500 uppercase tracking-wide">
                <tr>
                  <th className="text-left px-2 py-1 font-medium">
                    {t('strategy.budget.col.kind', { defaultValue: 'kind' })}
                  </th>
                  <th className="text-right px-2 py-1 font-medium">
                    {t('strategy.budget.col.tokens', { defaultValue: 'tokens' })}
                  </th>
                  <th className="text-left px-2 py-1 font-medium">
                    {t('strategy.budget.col.reason', { defaultValue: 'reason' })}
                  </th>
                </tr>
              </thead>
              <tbody>
                {budget.dropped_items.map((item, i) => (
                  <tr
                    key={`${item.kind}-${i}`}
                    className="border-t border-gray-700/50 bg-surface-light"
                  >
                    <td className="px-2 py-1 font-mono text-gray-200">{item.kind || '—'}</td>
                    <td className="px-2 py-1 font-mono text-right text-red-400">
                      {formatNumber(item.tokens)}
                    </td>
                    <td className="px-2 py-1 text-gray-400 break-words">
                      {item.reason || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

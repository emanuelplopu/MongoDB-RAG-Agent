import React from 'react'
import { useTranslation } from 'react-i18next'
import { PhaseMetrics } from '../types/telemetry'

interface TimelineWaterfallProps {
  phases: PhaseMetrics[]
  totalDuration?: number
  className?: string
}

const PHASE_COLORS: Record<string, string> = {
  analyze: 'bg-accent-blue',
  plan: 'bg-accent-purple',
  evaluate: 'bg-accent-green',
  synthesize: 'bg-accent-orange',
  search: 'bg-accent-teal',
  summarize: 'bg-accent-yellow',
  refine: 'bg-pink-400',
}

export function TimelineWaterfall({ phases, totalDuration, className = '' }: TimelineWaterfallProps) {
  const { t } = useTranslation()
  if (!phases || phases.length === 0) {
    return (
      <div className={`text-gray-500 text-sm italic py-4 text-center ${className}`}>
        {t('timeline.noData')}
      </div>
    )
  }

  const total = totalDuration || phases.reduce((sum, p) => sum + p.duration_ms, 0) || 1

  return (
    <div className={`space-y-1.5 ${className}`}>
      {/* Header */}
      <div className="flex justify-between text-xs text-gray-400 mb-2">
        <span>{t('timeline.phase')}</span>
        <span>{t('timeline.total')}: {(total / 1000).toFixed(1)}s</span>
      </div>

      {/* Bars */}
      {phases.map((phase, i) => {
        const widthPercent = Math.max((phase.duration_ms / total) * 100, 1)
        const color = PHASE_COLORS[phase.phase] || 'bg-gray-500'

        return (
          <div key={i} className="flex items-center gap-2">
            {/* Label */}
            <div className="w-20 text-xs text-gray-400 text-right truncate flex-shrink-0">
              {phase.phase}
            </div>

            {/* Bar container */}
            <div className="flex-1 h-6 bg-gray-100 dark:bg-surface rounded-md overflow-hidden relative">
              <div
                className={`h-full ${color} rounded-md transition-all duration-300 flex items-center px-2`}
                style={{ width: `${widthPercent}%` }}
              >
                {widthPercent > 15 && (
                  <span className="text-[10px] text-white/80 font-mono whitespace-nowrap">
                    {phase.duration_ms}ms
                  </span>
                )}
              </div>
            </div>

            {/* Duration label (outside bar if bar is small) */}
            {widthPercent <= 15 && (
              <span className="text-[10px] text-gray-400 font-mono w-14 flex-shrink-0">
                {phase.duration_ms}ms
              </span>
            )}

            {/* Tokens */}
            <div className="w-16 text-[10px] text-gray-500 text-right flex-shrink-0">
              {phase.tokens_used > 0 && `${phase.tokens_used} tok`}
            </div>
          </div>
        )
      })}

      {/* Legend */}
      <div className="flex flex-wrap gap-3 mt-3 pt-2 border-t border-gray-200 dark:border-gray-700/50">
        {Object.entries(PHASE_COLORS).map(([phase, color]) => (
          <div key={phase} className="flex items-center gap-1.5">
            <div className={`w-2.5 h-2.5 rounded-sm ${color}`} />
            <span className="text-[10px] text-gray-400 capitalize">{phase}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

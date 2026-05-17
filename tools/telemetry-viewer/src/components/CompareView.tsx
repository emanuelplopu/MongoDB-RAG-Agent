import React from 'react'

interface CompareViewProps {
  left: React.ReactNode
  right: React.ReactNode
  leftLabel?: string
  rightLabel?: string
  className?: string
}

export function CompareView({ left, right, leftLabel, rightLabel, className = '' }: CompareViewProps) {
  return (
    <div className={`grid grid-cols-2 gap-4 ${className}`}>
      {leftLabel && (
        <>
          <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider pb-1">{leftLabel}</div>
          <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider pb-1">{rightLabel}</div>
        </>
      )}
      <div className="overflow-auto">{left}</div>
      <div className="overflow-auto">{right}</div>
    </div>
  )
}

import React, { useState } from 'react'

interface MonospaceBlockProps {
  content: string
  maxLines?: number
  label?: string
  className?: string
}

export function MonospaceBlock({ content, maxLines = 20, label, className = '' }: MonospaceBlockProps) {
  const [expanded, setExpanded] = useState(false)
  const lines = content.split('\n')
  const shouldTruncate = lines.length > maxLines
  const displayContent = shouldTruncate && !expanded
    ? lines.slice(0, maxLines).join('\n')
    : content

  return (
    <div className={`relative ${className}`}>
      {label && (
        <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{label}</div>
      )}
      <pre className="bg-gray-100 dark:bg-surface rounded-lg p-3 overflow-x-auto text-xs font-mono text-gray-700 dark:text-gray-300 leading-relaxed whitespace-pre-wrap break-words max-h-96 overflow-y-auto">
        {displayContent}
      </pre>
      {shouldTruncate && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-1 text-[10px] text-accent-blue hover:text-accent-blue/80"
        >
          {expanded ? '▲ Collapse' : `▼ Show all (${lines.length} lines)`}
        </button>
      )}
    </div>
  )
}

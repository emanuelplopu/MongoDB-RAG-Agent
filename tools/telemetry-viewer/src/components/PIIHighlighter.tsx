import React from 'react'

interface PIIHighlighterProps {
  text: string
  interactive?: boolean  // Show hover tooltips
  className?: string
}

// PII type to color mapping
const PII_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  PERSON: { bg: 'bg-blue-500/20', text: 'text-blue-600 dark:text-blue-300', border: 'border-blue-500/40' },
  EMAIL: { bg: 'bg-purple-500/20', text: 'text-purple-600 dark:text-purple-300', border: 'border-purple-500/40' },
  PHONE: { bg: 'bg-green-500/20', text: 'text-green-600 dark:text-green-300', border: 'border-green-500/40' },
  ADDRESS: { bg: 'bg-orange-500/20', text: 'text-orange-600 dark:text-orange-300', border: 'border-orange-500/40' },
  COMPANY: { bg: 'bg-teal-500/20', text: 'text-teal-600 dark:text-teal-300', border: 'border-teal-500/40' },
  IBAN: { bg: 'bg-red-500/20', text: 'text-red-600 dark:text-red-300', border: 'border-red-500/40' },
}

const DEFAULT_COLOR = { bg: 'bg-gray-500/20', text: 'text-gray-600 dark:text-gray-300', border: 'border-gray-500/40' }

// Regex to match [PII:TYPE]alias[/PII]
const PII_REGEX = /\[PII:([A-Z_]+)\](.*?)\[\/PII\]/g

interface PIISegment {
  type: 'text' | 'pii'
  content: string
  piiType?: string
}

function parseText(text: string): PIISegment[] {
  const segments: PIISegment[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null

  // Reset regex
  PII_REGEX.lastIndex = 0

  while ((match = PII_REGEX.exec(text)) !== null) {
    // Add text before this match
    if (match.index > lastIndex) {
      segments.push({ type: 'text', content: text.slice(lastIndex, match.index) })
    }
    // Add PII segment
    segments.push({ type: 'pii', content: match[2], piiType: match[1] })
    lastIndex = match.index + match[0].length
  }

  // Add remaining text
  if (lastIndex < text.length) {
    segments.push({ type: 'text', content: text.slice(lastIndex) })
  }

  return segments
}

export function PIIHighlighter({ text, interactive = true, className = '' }: PIIHighlighterProps) {
  const segments = parseText(text)

  if (segments.length === 0) {
    return <span className={className}>{text}</span>
  }

  return (
    <span className={className}>
      {segments.map((segment, i) => {
        if (segment.type === 'text') {
          return <span key={i}>{segment.content}</span>
        }

        const colors = PII_COLORS[segment.piiType || ''] || DEFAULT_COLOR

        return (
          <span
            key={i}
            className={`inline-flex items-center px-1.5 py-0.5 mx-0.5 rounded border text-xs font-mono ${colors.bg} ${colors.text} ${colors.border} ${interactive ? 'cursor-help' : ''}`}
            title={interactive ? `PII Type: ${segment.piiType}` : undefined}
          >
            <span className="opacity-60 mr-1 text-[10px]">{segment.piiType}</span>
            {segment.content}
          </span>
        )
      })}
    </span>
  )
}

// Utility: count PII entities in text
export function countPIIEntities(text: string): Record<string, number> {
  const counts: Record<string, number> = {}
  PII_REGEX.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = PII_REGEX.exec(text)) !== null) {
    const type = match[1]
    counts[type] = (counts[type] || 0) + 1
  }
  return counts
}

// Utility: strip PII markers (get clean alias text)
export function stripPIIMarkers(text: string): string {
  return text.replace(PII_REGEX, '$2')
}

// Utility: check if text has PII markers
export function hasPIIMarkers(text: string): boolean {
  PII_REGEX.lastIndex = 0
  return PII_REGEX.test(text)
}

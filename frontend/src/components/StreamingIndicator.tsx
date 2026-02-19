/**
 * StreamingIndicator - Visual feedback during AI streaming responses
 * Shows elapsed time, processing phase, and active status indicator
 */

import { useState, useEffect, useRef } from 'react'

interface StreamingIndicatorProps {
  /** Whether streaming is currently active */
  isStreaming: boolean
  /** Current phase label (e.g., 'Analyzing...', 'Planning...') */
  phase?: string
  /** Show elapsed time */
  showElapsedTime?: boolean
  /** Custom class name */
  className?: string
  /** Size variant */
  size?: 'sm' | 'md' | 'lg'
  /** Show pulsing indicator dot */
  showPulse?: boolean
}

export default function StreamingIndicator({
  isStreaming,
  phase = 'Processing...',
  showElapsedTime = true,
  className = '',
  size = 'md',
  showPulse = true,
}: StreamingIndicatorProps) {
  const [elapsedTime, setElapsedTime] = useState(0)
  const startTimeRef = useRef<number | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (isStreaming) {
      startTimeRef.current = Date.now()
      setElapsedTime(0)
      
      intervalRef.current = setInterval(() => {
        if (startTimeRef.current) {
          setElapsedTime(Math.floor((Date.now() - startTimeRef.current) / 1000))
        }
      }, 1000)
    } else {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
      }
    }
  }, [isStreaming])

  if (!isStreaming) return null

  const formatTime = (seconds: number): string => {
    if (seconds < 60) return `${seconds}s`
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}m ${secs}s`
  }

  const sizeClasses = {
    sm: {
      container: 'gap-1.5',
      dot: 'w-2 h-2',
      text: 'text-xs',
      ring: 'w-3.5 h-3.5',
    },
    md: {
      container: 'gap-2',
      dot: 'w-2.5 h-2.5',
      text: 'text-sm',
      ring: 'w-4.5 h-4.5',
    },
    lg: {
      container: 'gap-2.5',
      dot: 'w-3 h-3',
      text: 'text-base',
      ring: 'w-5.5 h-5.5',
    },
  }

  const sizes = sizeClasses[size]

  return (
    <div className={`inline-flex items-center ${sizes.container} ${className}`}>
      {/* Pulsing indicator */}
      {showPulse && (
        <div className="relative flex items-center justify-center">
          <div className={`${sizes.dot} bg-green-500 rounded-full`} />
          <div className={`absolute ${sizes.dot} bg-green-500 rounded-full animate-ping opacity-75`} />
        </div>
      )}
      
      {/* Phase text */}
      <span className={`${sizes.text} text-secondary dark:text-gray-400 font-medium`}>
        {phase}
      </span>
      
      {/* Elapsed time */}
      {showElapsedTime && (
        <span className={`${sizes.text} text-gray-400 dark:text-gray-500 tabular-nums`}>
          {formatTime(elapsedTime)}
        </span>
      )}
    </div>
  )
}

/**
 * Compact streaming dot - just shows a pulsing dot with optional label
 */
export function StreamingDot({
  isStreaming,
  label,
  className = '',
}: {
  isStreaming: boolean
  label?: string
  className?: string
}) {
  if (!isStreaming) return null

  return (
    <div className={`inline-flex items-center gap-1.5 ${className}`}>
      <div className="relative">
        <div className="w-2 h-2 bg-green-500 rounded-full" />
        <div className="absolute inset-0 w-2 h-2 bg-green-500 rounded-full animate-ping opacity-75" />
      </div>
      {label && (
        <span className="text-xs text-green-600 dark:text-green-400 font-medium">
          {label}
        </span>
      )}
    </div>
  )
}

/**
 * Typing indicator with animated dots
 */
export function TypingIndicator({ className = '' }: { className?: string }) {
  return (
    <div className={`flex items-center gap-1 ${className}`}>
      <div className="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full typing-dot" />
      <div className="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full typing-dot" />
      <div className="w-2 h-2 bg-gray-400 dark:bg-gray-500 rounded-full typing-dot" />
    </div>
  )
}

/**
 * Processing phase indicator with icon
 */
export function ProcessingPhase({
  phase,
  icon,
  className = '',
}: {
  phase: string
  icon?: React.ReactNode
  className?: string
}) {
  return (
    <div className={`inline-flex items-center gap-2 px-3 py-1.5 bg-primary-50 dark:bg-primary-900/30 rounded-full ${className}`}>
      {icon || (
        <svg className="w-4 h-4 text-primary animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
      )}
      <span className="text-sm text-primary-700 dark:text-primary-300 font-medium">
        {phase}
      </span>
    </div>
  )
}

/**
 * Completion indicator - shows success with optional message
 */
export function CompletionIndicator({
  message = 'Complete',
  className = '',
}: {
  message?: string
  className?: string
}) {
  return (
    <div className={`inline-flex items-center gap-2 ${className}`}>
      <div className="w-5 h-5 bg-green-100 dark:bg-green-900/30 rounded-full flex items-center justify-center">
        <svg className="w-3 h-3 text-green-600 dark:text-green-400" fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
        </svg>
      </div>
      <span className="text-sm text-green-600 dark:text-green-400 font-medium">
        {message}
      </span>
    </div>
  )
}

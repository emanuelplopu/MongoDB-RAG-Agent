/**
 * ProgressBar - Progress indicator components
 * Provides determinate and indeterminate progress bars
 */

import React from 'react'

interface ProgressBarProps {
  /** Progress value (0-100). If undefined, shows indeterminate animation */
  value?: number
  /** Size variant */
  size?: 'xs' | 'sm' | 'md' | 'lg'
  /** Color variant */
  variant?: 'primary' | 'success' | 'warning' | 'error'
  /** Show percentage label */
  showLabel?: boolean
  /** Custom label text */
  label?: string
  /** Additional class names */
  className?: string
  /** Animate the progress change */
  animated?: boolean
}

/**
 * Linear progress bar
 */
export default function ProgressBar({
  value,
  size = 'sm',
  variant = 'primary',
  showLabel = false,
  label,
  className = '',
  animated = true,
}: ProgressBarProps) {
  const isIndeterminate = value === undefined

  const sizeClasses = {
    xs: 'h-1',
    sm: 'h-2',
    md: 'h-3',
    lg: 'h-4',
  }

  const colorClasses = {
    primary: 'bg-primary',
    success: 'bg-green-500',
    warning: 'bg-yellow-500',
    error: 'bg-red-500',
  }

  const bgClasses = {
    primary: 'bg-primary-100 dark:bg-primary-900/30',
    success: 'bg-green-100 dark:bg-green-900/30',
    warning: 'bg-yellow-100 dark:bg-yellow-900/30',
    error: 'bg-red-100 dark:bg-red-900/30',
  }

  return (
    <div className={`w-full ${className}`}>
      {(showLabel || label) && (
        <div className="flex justify-between mb-1 text-xs text-secondary dark:text-gray-400">
          <span>{label || 'Progress'}</span>
          {!isIndeterminate && <span>{Math.round(value)}%</span>}
        </div>
      )}
      <div className={`w-full ${sizeClasses[size]} ${bgClasses[variant]} rounded-full overflow-hidden`}>
        {isIndeterminate ? (
          <div
            className={`h-full w-1/4 ${colorClasses[variant]} rounded-full animate-progress-indeterminate`}
          />
        ) : (
          <div
            className={`h-full ${colorClasses[variant]} rounded-full ${animated ? 'transition-all duration-300 ease-out' : ''}`}
            style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
          />
        )}
      </div>
    </div>
  )
}

/**
 * Circular progress indicator
 */
export function CircularProgress({
  value,
  size = 40,
  strokeWidth = 4,
  variant = 'primary',
  showLabel = false,
  className = '',
}: {
  value?: number
  size?: number
  strokeWidth?: number
  variant?: 'primary' | 'success' | 'warning' | 'error'
  showLabel?: boolean
  className?: string
}) {
  const isIndeterminate = value === undefined
  const radius = (size - strokeWidth) / 2
  const circumference = radius * 2 * Math.PI
  const offset = isIndeterminate ? 0 : circumference - ((value || 0) / 100) * circumference

  const colorClasses = {
    primary: 'text-primary',
    success: 'text-green-500',
    warning: 'text-yellow-500',
    error: 'text-red-500',
  }

  return (
    <div className={`relative inline-flex items-center justify-center ${className}`}>
      <svg
        width={size}
        height={size}
        className={`${isIndeterminate ? 'animate-spin' : ''}`}
      >
        {/* Background circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          className="text-gray-200 dark:text-gray-700"
        />
        {/* Progress circle */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={isIndeterminate ? circumference * 0.75 : offset}
          className={`${colorClasses[variant]} transition-all duration-300 ease-out`}
          style={{ transform: 'rotate(-90deg)', transformOrigin: '50% 50%' }}
        />
      </svg>
      {showLabel && !isIndeterminate && (
        <span className="absolute text-xs font-medium text-primary-900 dark:text-gray-200">
          {Math.round(value || 0)}%
        </span>
      )}
    </div>
  )
}

/**
 * Step progress indicator
 */
export function StepProgress({
  steps,
  currentStep,
  className = '',
}: {
  steps: string[]
  currentStep: number
  className?: string
}) {
  return (
    <div className={`flex items-center ${className}`}>
      {steps.map((step, index) => (
        <React.Fragment key={index}>
          <div className="flex flex-col items-center">
            <div
              className={`
                w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium
                transition-all duration-300
                ${index < currentStep
                  ? 'bg-primary text-white'
                  : index === currentStep
                    ? 'bg-primary-100 dark:bg-primary-900 text-primary border-2 border-primary'
                    : 'bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
                }
              `}
            >
              {index < currentStep ? (
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
              ) : (
                index + 1
              )}
            </div>
            <span className={`mt-1 text-xs ${index <= currentStep ? 'text-primary-700 dark:text-primary-300' : 'text-gray-500 dark:text-gray-400'}`}>
              {step}
            </span>
          </div>
          {index < steps.length - 1 && (
            <div
              className={`flex-1 h-1 mx-2 rounded-full transition-all duration-300 ${
                index < currentStep ? 'bg-primary' : 'bg-gray-200 dark:bg-gray-700'
              }`}
            />
          )}
        </React.Fragment>
      ))}
    </div>
  )
}

/**
 * Operation status indicator
 */
export function OperationStatus({
  status,
  message,
  className = '',
}: {
  status: 'idle' | 'loading' | 'success' | 'error'
  message?: string
  className?: string
}) {
  const statusConfig = {
    idle: {
      icon: null,
      color: 'text-gray-400',
      bg: 'bg-gray-100 dark:bg-gray-800',
    },
    loading: {
      icon: (
        <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
        </svg>
      ),
      color: 'text-primary',
      bg: 'bg-primary-50 dark:bg-primary-900/30',
    },
    success: {
      icon: (
        <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
        </svg>
      ),
      color: 'text-green-500',
      bg: 'bg-green-50 dark:bg-green-900/30',
    },
    error: {
      icon: (
        <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
          <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
        </svg>
      ),
      color: 'text-red-500',
      bg: 'bg-red-50 dark:bg-red-900/30',
    },
  }

  const config = statusConfig[status]

  if (status === 'idle' && !message) return null

  return (
    <div className={`flex items-center gap-2 px-3 py-2 rounded-lg ${config.bg} ${className}`}>
      {config.icon && <span className={config.color}>{config.icon}</span>}
      {message && <span className={`text-sm ${config.color}`}>{message}</span>}
    </div>
  )
}

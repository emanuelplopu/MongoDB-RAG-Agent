/**
 * LoadingSkeleton - Skeleton loading placeholders for better perceived performance
 * Provides shimmer animation while content is loading
 */

import React from 'react'

interface SkeletonProps {
  className?: string
  /** Width - can be number (pixels) or string (e.g., '100%', '20rem') */
  width?: number | string
  /** Height - can be number (pixels) or string */
  height?: number | string
  /** Border radius variant */
  variant?: 'text' | 'circular' | 'rectangular' | 'rounded'
  /** Enable shimmer animation */
  animate?: boolean
}

/**
 * Base skeleton component
 */
export function Skeleton({
  className = '',
  width,
  height,
  variant = 'text',
  animate = true,
}: SkeletonProps) {
  const variantClasses = {
    text: 'rounded',
    circular: 'rounded-full',
    rectangular: 'rounded-none',
    rounded: 'rounded-xl',
  }

  const style: React.CSSProperties = {
    width: typeof width === 'number' ? `${width}px` : width,
    height: typeof height === 'number' ? `${height}px` : height,
  }

  return (
    <div
      className={`
        bg-gray-200 dark:bg-gray-700
        ${variantClasses[variant]}
        ${animate ? 'animate-shimmer bg-gradient-to-r from-gray-200 via-gray-100 to-gray-200 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 bg-[length:200%_100%]' : ''}
        ${className}
      `}
      style={style}
    />
  )
}

/**
 * Text line skeleton
 */
export function SkeletonText({ 
  lines = 1, 
  className = '',
  lastLineWidth = '60%',
}: { 
  lines?: number
  className?: string
  lastLineWidth?: string
}) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          height={16}
          width={i === lines - 1 && lines > 1 ? lastLineWidth : '100%'}
          variant="text"
        />
      ))}
    </div>
  )
}

/**
 * Avatar skeleton
 */
export function SkeletonAvatar({ 
  size = 40,
  className = '',
}: { 
  size?: number
  className?: string
}) {
  return (
    <Skeleton
      width={size}
      height={size}
      variant="circular"
      className={className}
    />
  )
}

/**
 * Card skeleton
 */
export function SkeletonCard({ className = '' }: { className?: string }) {
  return (
    <div className={`rounded-2xl bg-surface dark:bg-gray-800 p-4 shadow-elevation-1 ${className}`}>
      <div className="flex items-start gap-3">
        <SkeletonAvatar size={48} />
        <div className="flex-1 space-y-2">
          <Skeleton height={20} width="40%" />
          <Skeleton height={14} width="60%" />
        </div>
      </div>
      <div className="mt-4">
        <SkeletonText lines={3} />
      </div>
    </div>
  )
}

/**
 * Chat message skeleton
 */
export function SkeletonMessage({ 
  isUser = false,
  className = '',
}: { 
  isUser?: boolean
  className?: string
}) {
  return (
    <div className={`flex gap-4 ${isUser ? 'flex-row-reverse' : ''} ${className}`}>
      <SkeletonAvatar size={32} />
      <div className={`flex-1 max-w-[85%] ${isUser ? 'text-right' : ''}`}>
        <div className={`inline-block rounded-2xl px-4 py-3 ${
          isUser
            ? 'bg-primary/20'
            : 'bg-surface-variant dark:bg-gray-700'
        }`}>
          <SkeletonText lines={2} lastLineWidth="80%" />
        </div>
      </div>
    </div>
  )
}

/**
 * Search result skeleton
 */
export function SkeletonSearchResult({ className = '' }: { className?: string }) {
  return (
    <div className={`rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1 ${className}`}>
      <div className="flex items-start justify-between mb-3">
        <div className="space-y-2">
          <Skeleton height={18} width={200} />
          <Skeleton height={14} width={150} />
        </div>
        <Skeleton height={24} width={80} variant="rounded" />
      </div>
      <SkeletonText lines={3} />
    </div>
  )
}

/**
 * Table row skeleton
 */
export function SkeletonTableRow({ 
  columns = 4,
  className = '',
}: { 
  columns?: number
  className?: string
}) {
  return (
    <tr className={className}>
      {Array.from({ length: columns }).map((_, i) => (
        <td key={i} className="px-3 py-3">
          <Skeleton height={16} width={i === 0 ? '70%' : '50%'} />
        </td>
      ))}
    </tr>
  )
}

/**
 * Document list skeleton
 */
export function SkeletonDocumentList({ 
  count = 5,
  className = '',
}: { 
  count?: number
  className?: string
}) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 p-2 rounded-lg">
          <Skeleton width={20} height={20} variant="rounded" />
          <Skeleton height={16} width="60%" />
          <Skeleton height={14} width={60} className="ml-auto" />
        </div>
      ))}
    </div>
  )
}

/**
 * Sidebar skeleton
 */
export function SkeletonSidebar({ className = '' }: { className?: string }) {
  return (
    <div className={`space-y-4 p-4 ${className}`}>
      <Skeleton height={40} variant="rounded" />
      <div className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="flex items-center gap-2 p-2">
            <SkeletonAvatar size={24} />
            <Skeleton height={14} width="80%" />
          </div>
        ))}
      </div>
    </div>
  )
}

export default Skeleton

import React, { useState } from 'react'

interface CollapsibleProps {
  title: string
  badge?: string | number
  defaultOpen?: boolean
  children: React.ReactNode
  className?: string
}

export function Collapsible({ title, badge, defaultOpen = false, children, className = '' }: CollapsibleProps) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div className={`border border-gray-200 dark:border-gray-700/50 rounded-lg overflow-hidden ${className}`}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-2.5 bg-gray-50 dark:bg-surface-light hover:bg-gray-100 dark:hover:bg-surface-lighter transition-colors text-left"
      >
        <div className="flex items-center gap-2">
          <span className={`text-xs transition-transform ${open ? 'rotate-90' : ''}`}>▶</span>
          <span className="text-sm font-medium text-gray-800 dark:text-gray-200">{title}</span>
          {badge !== undefined && (
            <span className="px-1.5 py-0.5 text-[10px] rounded-full bg-accent-blue/20 text-accent-blue font-mono">
              {badge}
            </span>
          )}
        </div>
      </button>
      {open && (
        <div className="px-4 py-3 bg-gray-50/50 dark:bg-surface/50 border-t border-gray-200 dark:border-gray-700/30">
          {children}
        </div>
      )}
    </div>
  )
}

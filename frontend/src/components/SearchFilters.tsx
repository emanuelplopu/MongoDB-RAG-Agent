/**
 * SearchFilters - Advanced filtering UI for search
 * Provides faceted search with collapsible filter groups
 */

import { useState, useCallback } from 'react'
import {
  FunnelIcon,
  XMarkIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  CalendarIcon,
  DocumentTextIcon,
  TagIcon,
} from '@heroicons/react/24/outline'

export interface FilterOption {
  id: string
  label: string
  count?: number
}

export interface FilterGroup {
  id: string
  label: string
  type: 'checkbox' | 'radio' | 'date-range'
  options?: FilterOption[]
  icon?: React.ComponentType<{ className?: string }>
}

export interface FilterValue {
  [groupId: string]: string[] | { from?: string; to?: string }
}

interface SearchFiltersProps {
  /** Filter groups to display */
  groups: FilterGroup[]
  /** Current filter values */
  values: FilterValue
  /** Called when filters change */
  onChange: (values: FilterValue) => void
  /** Called to clear all filters */
  onClear: () => void
  /** Additional class name */
  className?: string
  /** Whether to show in compact/collapsible mode */
  compact?: boolean
}

export default function SearchFilters({
  groups,
  values,
  onChange,
  onClear,
  className = '',
  compact = false,
}: SearchFiltersProps) {
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    new Set(groups.map(g => g.id))
  )
  const [isExpanded, setIsExpanded] = useState(!compact)

  const toggleGroup = (groupId: string) => {
    setExpandedGroups(prev => {
      const next = new Set(prev)
      if (next.has(groupId)) {
        next.delete(groupId)
      } else {
        next.add(groupId)
      }
      return next
    })
  }

  const handleCheckboxChange = (groupId: string, optionId: string) => {
    const current = (values[groupId] as string[]) || []
    const updated = current.includes(optionId)
      ? current.filter(id => id !== optionId)
      : [...current, optionId]
    
    onChange({
      ...values,
      [groupId]: updated,
    })
  }

  const handleRadioChange = (groupId: string, optionId: string) => {
    onChange({
      ...values,
      [groupId]: [optionId],
    })
  }

  const handleDateChange = (groupId: string, field: 'from' | 'to', value: string) => {
    const current = (values[groupId] as { from?: string; to?: string }) || {}
    onChange({
      ...values,
      [groupId]: { ...current, [field]: value },
    })
  }

  // Count active filters
  const activeFilterCount = Object.entries(values).reduce((count, [, value]) => {
    if (Array.isArray(value)) {
      return count + value.length
    }
    if (typeof value === 'object') {
      return count + (value.from ? 1 : 0) + (value.to ? 1 : 0)
    }
    return count
  }, 0)

  if (compact && !isExpanded) {
    return (
      <button
        onClick={() => setIsExpanded(true)}
        className={`
          flex items-center gap-2 px-4 py-2 rounded-xl 
          bg-surface-variant dark:bg-gray-700 
          text-gray-700 dark:text-gray-300
          hover:bg-gray-200 dark:hover:bg-gray-600
          transition-colors
          ${className}
        `}
      >
        <FunnelIcon className="h-5 w-5" />
        <span>Filters</span>
        {activeFilterCount > 0 && (
          <span className="w-5 h-5 rounded-full bg-primary text-white text-xs flex items-center justify-center">
            {activeFilterCount}
          </span>
        )}
      </button>
    )
  }

  return (
    <div className={`bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center gap-2">
          <FunnelIcon className="h-5 w-5 text-gray-500" />
          <span className="font-medium text-gray-900 dark:text-gray-100">Filters</span>
          {activeFilterCount > 0 && (
            <span className="px-2 py-0.5 rounded-full bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300 text-xs font-medium">
              {activeFilterCount} active
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {activeFilterCount > 0 && (
            <button
              onClick={onClear}
              className="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
            >
              Clear all
            </button>
          )}
          {compact && (
            <button
              onClick={() => setIsExpanded(false)}
              className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
            >
              <XMarkIcon className="h-5 w-5" />
            </button>
          )}
        </div>
      </div>

      {/* Filter groups */}
      <div className="divide-y divide-gray-100 dark:divide-gray-700">
        {groups.map(group => {
          const isGroupExpanded = expandedGroups.has(group.id)
          const Icon = group.icon || TagIcon
          const groupValue = values[group.id]
          const hasValue = Array.isArray(groupValue) 
            ? groupValue.length > 0 
            : groupValue && (groupValue.from || groupValue.to)

          return (
            <div key={group.id} className="px-4">
              {/* Group header */}
              <button
                onClick={() => toggleGroup(group.id)}
                className="w-full flex items-center justify-between py-3 text-left"
              >
                <div className="flex items-center gap-2">
                  <Icon className="h-4 w-4 text-gray-400" />
                  <span className="font-medium text-gray-700 dark:text-gray-300">
                    {group.label}
                  </span>
                  {hasValue && (
                    <span className="w-2 h-2 rounded-full bg-primary" />
                  )}
                </div>
                {isGroupExpanded ? (
                  <ChevronUpIcon className="h-4 w-4 text-gray-400" />
                ) : (
                  <ChevronDownIcon className="h-4 w-4 text-gray-400" />
                )}
              </button>

              {/* Group content */}
              {isGroupExpanded && (
                <div className="pb-3 space-y-2">
                  {group.type === 'checkbox' && group.options?.map(option => {
                    const isChecked = ((groupValue as string[]) || []).includes(option.id)
                    return (
                      <label
                        key={option.id}
                        className="flex items-center gap-3 py-1 cursor-pointer group"
                      >
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => handleCheckboxChange(group.id, option.id)}
                          className="w-4 h-4 rounded border-gray-300 dark:border-gray-600 text-primary focus:ring-primary"
                        />
                        <span className={`flex-1 text-sm ${isChecked ? 'text-gray-900 dark:text-gray-100' : 'text-gray-600 dark:text-gray-400'}`}>
                          {option.label}
                        </span>
                        {option.count !== undefined && (
                          <span className="text-xs text-gray-400">
                            {option.count}
                          </span>
                        )}
                      </label>
                    )
                  })}

                  {group.type === 'radio' && group.options?.map(option => {
                    const isChecked = ((groupValue as string[]) || [])[0] === option.id
                    return (
                      <label
                        key={option.id}
                        className="flex items-center gap-3 py-1 cursor-pointer"
                      >
                        <input
                          type="radio"
                          name={group.id}
                          checked={isChecked}
                          onChange={() => handleRadioChange(group.id, option.id)}
                          className="w-4 h-4 border-gray-300 dark:border-gray-600 text-primary focus:ring-primary"
                        />
                        <span className={`flex-1 text-sm ${isChecked ? 'text-gray-900 dark:text-gray-100' : 'text-gray-600 dark:text-gray-400'}`}>
                          {option.label}
                        </span>
                        {option.count !== undefined && (
                          <span className="text-xs text-gray-400">
                            {option.count}
                          </span>
                        )}
                      </label>
                    )
                  })}

                  {group.type === 'date-range' && (
                    <div className="space-y-2">
                      <div className="flex items-center gap-2">
                        <CalendarIcon className="h-4 w-4 text-gray-400" />
                        <input
                          type="date"
                          value={((groupValue as { from?: string; to?: string })?.from) || ''}
                          onChange={(e) => handleDateChange(group.id, 'from', e.target.value)}
                          className="flex-1 px-2 py-1.5 text-sm bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                          placeholder="From"
                        />
                        <span className="text-gray-400">to</span>
                        <input
                          type="date"
                          value={((groupValue as { from?: string; to?: string })?.to) || ''}
                          onChange={(e) => handleDateChange(group.id, 'to', e.target.value)}
                          className="flex-1 px-2 py-1.5 text-sm bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary"
                          placeholder="To"
                        />
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/**
 * ActiveFilters - Display active filters as chips
 */
interface ActiveFiltersProps {
  groups: FilterGroup[]
  values: FilterValue
  onRemove: (groupId: string, optionId: string) => void
  onClear: () => void
  className?: string
}

export function ActiveFilters({
  groups,
  values,
  onRemove,
  onClear,
  className = '',
}: ActiveFiltersProps) {
  // Build list of active filter chips
  const chips: { groupId: string; groupLabel: string; optionId: string; optionLabel: string }[] = []
  
  Object.entries(values).forEach(([groupId, value]) => {
    const group = groups.find(g => g.id === groupId)
    if (!group) return
    
    if (Array.isArray(value)) {
      value.forEach(optionId => {
        const option = group.options?.find(o => o.id === optionId)
        if (option) {
          chips.push({
            groupId,
            groupLabel: group.label,
            optionId,
            optionLabel: option.label,
          })
        }
      })
    }
  })

  if (chips.length === 0) return null

  return (
    <div className={`flex flex-wrap items-center gap-2 ${className}`}>
      <span className="text-sm text-gray-500 dark:text-gray-400">Active filters:</span>
      {chips.map(chip => (
        <span
          key={`${chip.groupId}-${chip.optionId}`}
          className="inline-flex items-center gap-1 px-2 py-1 bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300 rounded-lg text-sm"
        >
          <span className="text-xs text-primary-500 dark:text-primary-400">{chip.groupLabel}:</span>
          {chip.optionLabel}
          <button
            onClick={() => onRemove(chip.groupId, chip.optionId)}
            className="ml-1 hover:text-primary-900 dark:hover:text-primary-100"
          >
            <XMarkIcon className="h-3.5 w-3.5" />
          </button>
        </span>
      ))}
      <button
        onClick={onClear}
        className="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
      >
        Clear all
      </button>
    </div>
  )
}

/**
 * Hook to manage filter state
 */
export function useSearchFilters(initialValues: FilterValue = {}) {
  const [values, setValues] = useState<FilterValue>(initialValues)

  const handleChange = useCallback((newValues: FilterValue) => {
    setValues(newValues)
  }, [])

  const handleClear = useCallback(() => {
    setValues({})
  }, [])

  const handleRemove = useCallback((groupId: string, optionId: string) => {
    setValues(prev => {
      const current = (prev[groupId] as string[]) || []
      return {
        ...prev,
        [groupId]: current.filter(id => id !== optionId),
      }
    })
  }, [])

  return {
    values,
    setValues: handleChange,
    clear: handleClear,
    remove: handleRemove,
  }
}

// Example filter groups
export const exampleFilterGroups: FilterGroup[] = [
  {
    id: 'file_type',
    label: 'File Type',
    type: 'checkbox',
    icon: DocumentTextIcon,
    options: [
      { id: 'pdf', label: 'PDF', count: 42 },
      { id: 'docx', label: 'Word Document', count: 28 },
      { id: 'txt', label: 'Text File', count: 15 },
      { id: 'md', label: 'Markdown', count: 8 },
    ],
  },
  {
    id: 'date_range',
    label: 'Date Added',
    type: 'date-range',
    icon: CalendarIcon,
  },
  {
    id: 'source',
    label: 'Source',
    type: 'checkbox',
    options: [
      { id: 'upload', label: 'Uploaded', count: 50 },
      { id: 'google_drive', label: 'Google Drive', count: 30 },
      { id: 'dropbox', label: 'Dropbox', count: 13 },
    ],
  },
]

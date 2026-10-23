/**
 * Form controls for settings pages: section, toggle, select, number and slider.
 */

import { ReactNode } from 'react'

/**
 * Settings Section Component
 */
interface SettingsSectionProps {
  title: string
  description?: string
  children: ReactNode
}

export function SettingsSection({ title, description, children }: SettingsSectionProps) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{title}</h3>
        {description && (
          <p className="text-sm text-gray-500 dark:text-gray-400">{description}</p>
        )}
      </div>
      <div className="space-y-3">
        {children}
      </div>
    </div>
  )
}

/**
 * Settings Toggle Component
 */
interface SettingsToggleProps {
  label: string
  description?: string
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
}

export function SettingsToggle({
  label,
  description,
  checked,
  onChange,
  disabled = false,
}: SettingsToggleProps) {
  return (
    <label className={`flex items-center justify-between py-2 ${disabled ? 'opacity-50' : ''}`}>
      <div className="flex-1">
        <div className="font-medium text-gray-700 dark:text-gray-300">{label}</div>
        {description && (
          <div className="text-sm text-gray-500 dark:text-gray-400">{description}</div>
        )}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        className={`
          relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full
          border-2 border-transparent transition-colors duration-200 ease-in-out
          focus:outline-hidden focus:ring-2 focus:ring-primary focus:ring-offset-2
          disabled:cursor-not-allowed
          ${checked ? 'bg-primary' : 'bg-gray-200 dark:bg-gray-600'}
        `}
      >
        <span
          className={`
            pointer-events-none inline-block h-5 w-5 rounded-full
            bg-white shadow ring-0 transition duration-200 ease-in-out
            ${checked ? 'translate-x-5' : 'translate-x-0'}
          `}
        />
      </button>
    </label>
  )
}

/**
 * Settings Select Component
 */
interface SettingsSelectProps<T extends string> {
  label: string
  description?: string
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
  disabled?: boolean
}

export function SettingsSelect<T extends string>({
  label,
  description,
  value,
  options,
  onChange,
  disabled = false,
}: SettingsSelectProps<T>) {
  return (
    <div className={`flex items-center justify-between py-2 ${disabled ? 'opacity-50' : ''}`}>
      <div className="flex-1">
        <div className="font-medium text-gray-700 dark:text-gray-300">{label}</div>
        {description && (
          <div className="text-sm text-gray-500 dark:text-gray-400">{description}</div>
        )}
      </div>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        disabled={disabled}
        className="px-3 py-1.5 bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 text-sm focus:outline-hidden focus:ring-2 focus:ring-primary disabled:cursor-not-allowed"
      >
        {options.map(option => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  )
}

/**
 * Settings Number Input Component
 */
interface SettingsNumberProps {
  label: string
  description?: string
  value: number
  min?: number
  max?: number
  step?: number
  onChange: (value: number) => void
  disabled?: boolean
  suffix?: string
}

export function SettingsNumber({
  label,
  description,
  value,
  min,
  max,
  step = 1,
  onChange,
  disabled = false,
  suffix,
}: SettingsNumberProps) {
  return (
    <div className={`flex items-center justify-between py-2 ${disabled ? 'opacity-50' : ''}`}>
      <div className="flex-1">
        <div className="font-medium text-gray-700 dark:text-gray-300">{label}</div>
        {description && (
          <div className="text-sm text-gray-500 dark:text-gray-400">{description}</div>
        )}
      </div>
      <div className="flex items-center gap-2">
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={(e) => onChange(Number(e.target.value))}
          disabled={disabled}
          className="w-20 px-3 py-1.5 bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 text-sm text-right focus:outline-hidden focus:ring-2 focus:ring-primary disabled:cursor-not-allowed"
        />
        {suffix && (
          <span className="text-sm text-gray-500 dark:text-gray-400">{suffix}</span>
        )}
      </div>
    </div>
  )
}

/**
 * Settings Slider Component
 */
interface SettingsSliderProps {
  label: string
  description?: string
  value: number
  min: number
  max: number
  step?: number
  onChange: (value: number) => void
  disabled?: boolean
  showValue?: boolean
  valueFormatter?: (value: number) => string
}

export function SettingsSlider({
  label,
  description,
  value,
  min,
  max,
  step = 1,
  onChange,
  disabled = false,
  showValue = true,
  valueFormatter = (v) => String(v),
}: SettingsSliderProps) {
  return (
    <div className={`py-2 ${disabled ? 'opacity-50' : ''}`}>
      <div className="flex items-center justify-between mb-2">
        <div>
          <div className="font-medium text-gray-700 dark:text-gray-300">{label}</div>
          {description && (
            <div className="text-sm text-gray-500 dark:text-gray-400">{description}</div>
          )}
        </div>
        {showValue && (
          <span className="text-sm font-medium text-primary-600 dark:text-primary-400">
            {valueFormatter(value)}
          </span>
        )}
      </div>
      <input
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
        disabled={disabled}
        className="w-full h-2 bg-gray-200 dark:bg-gray-600 rounded-lg appearance-none cursor-pointer accent-primary disabled:cursor-not-allowed"
      />
    </div>
  )
}

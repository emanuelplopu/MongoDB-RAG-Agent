/**
 * UserPreferencesContext - Manages user preferences and settings
 * Persists settings to localStorage with automatic sync
 */

import { createContext, useContext, useCallback, useMemo, ReactNode } from 'react'
import { useLocalStorage } from '../hooks/useLocalStorage'

// User preferences interface
interface UserPreferences {
  // Search settings
  defaultSearchType: 'hybrid' | 'semantic' | 'text'
  defaultMatchCount: number
  showSearchHistory: boolean
  
  // Display settings
  uiDensity: 'compact' | 'comfortable' | 'spacious'
  itemsPerPage: number
  showLineNumbers: boolean
  codeTheme: 'light' | 'dark' | 'system'
  
  // Chat settings
  streamingEnabled: boolean
  showTimestamps: boolean
  messageGrouping: boolean
  enterToSend: boolean
  
  // Notifications
  soundEnabled: boolean
  notificationsEnabled: boolean
  showToastDuration: number
  
  // Advanced
  developerMode: boolean
  experimentalFeatures: boolean
  autoSaveEnabled: boolean
  autoSaveInterval: number
}

// Default preferences
const defaultPreferences: UserPreferences = {
  // Search settings
  defaultSearchType: 'hybrid',
  defaultMatchCount: 10,
  showSearchHistory: true,
  
  // Display settings
  uiDensity: 'comfortable',
  itemsPerPage: 20,
  showLineNumbers: true,
  codeTheme: 'system',
  
  // Chat settings
  streamingEnabled: true,
  showTimestamps: false,
  messageGrouping: true,
  enterToSend: true,
  
  // Notifications
  soundEnabled: false,
  notificationsEnabled: true,
  showToastDuration: 3000,
  
  // Advanced
  developerMode: false,
  experimentalFeatures: false,
  autoSaveEnabled: true,
  autoSaveInterval: 5000,
}

// Context type
interface UserPreferencesContextType {
  preferences: UserPreferences
  setPreference: <K extends keyof UserPreferences>(key: K, value: UserPreferences[K]) => void
  setPreferences: (updates: Partial<UserPreferences>) => void
  resetPreferences: () => void
  resetPreference: <K extends keyof UserPreferences>(key: K) => void
  isDefault: <K extends keyof UserPreferences>(key: K) => boolean
}

const UserPreferencesContext = createContext<UserPreferencesContextType | null>(null)

const STORAGE_KEY = 'user_preferences'

interface UserPreferencesProviderProps {
  children: ReactNode
}

export function UserPreferencesProvider({ children }: UserPreferencesProviderProps) {
  const [preferences, setPreferencesState] = useLocalStorage<UserPreferences>(
    STORAGE_KEY,
    defaultPreferences
  )

  const setPreference = useCallback(<K extends keyof UserPreferences>(
    key: K,
    value: UserPreferences[K]
  ) => {
    setPreferencesState(prev => ({
      ...prev,
      [key]: value,
    }))
  }, [setPreferencesState])

  const setPreferences = useCallback((updates: Partial<UserPreferences>) => {
    setPreferencesState(prev => ({
      ...prev,
      ...updates,
    }))
  }, [setPreferencesState])

  const resetPreferences = useCallback(() => {
    setPreferencesState(defaultPreferences)
  }, [setPreferencesState])

  const resetPreference = useCallback(<K extends keyof UserPreferences>(key: K) => {
    setPreferencesState(prev => ({
      ...prev,
      [key]: defaultPreferences[key],
    }))
  }, [setPreferencesState])

  const isDefault = useCallback(<K extends keyof UserPreferences>(key: K): boolean => {
    return preferences[key] === defaultPreferences[key]
  }, [preferences])

  const value = useMemo(() => ({
    preferences,
    setPreference,
    setPreferences,
    resetPreferences,
    resetPreference,
    isDefault,
  }), [preferences, setPreference, setPreferences, resetPreferences, resetPreference, isDefault])

  return (
    <UserPreferencesContext.Provider value={value}>
      {children}
    </UserPreferencesContext.Provider>
  )
}

export function useUserPreferences() {
  const context = useContext(UserPreferencesContext)
  if (!context) {
    throw new Error('useUserPreferences must be used within a UserPreferencesProvider')
  }
  return context
}

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
          relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full 
          border-2 border-transparent transition-colors duration-200 ease-in-out
          focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2
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
        className="px-3 py-1.5 bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 text-sm focus:outline-none focus:ring-2 focus:ring-primary disabled:cursor-not-allowed"
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
          className="w-20 px-3 py-1.5 bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 text-sm text-right focus:outline-none focus:ring-2 focus:ring-primary disabled:cursor-not-allowed"
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

export default UserPreferencesContext

import { useCallback, useMemo, useEffect, ReactNode } from 'react'
import { useLocalStorage } from '../hooks/useLocalStorage'
import { useOptionalSettingsSync, SETTINGS_SYNCED_EVENT } from './SettingsSyncContext'
import { UserPreferencesSync } from '../api/client'
import { UserPreferences, UserPreferencesContext } from './UserPreferencesContext'

// Default preferences
const defaultPreferences: UserPreferences = {
  // Model settings
  defaultModel: '',  // Empty means use system default

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

const STORAGE_KEY = 'user_preferences'

// Map from DB snake_case keys to local camelCase keys
const SYNC_KEY_MAP: Record<string, keyof UserPreferences> = {
  default_model: 'defaultModel',
  default_search_type: 'defaultSearchType',
  default_match_count: 'defaultMatchCount',
  ui_density: 'uiDensity',
  items_per_page: 'itemsPerPage',
  show_line_numbers: 'showLineNumbers',
  code_theme: 'codeTheme',
  streaming_enabled: 'streamingEnabled',
  show_timestamps: 'showTimestamps',
  message_grouping: 'messageGrouping',
  enter_to_send: 'enterToSend',
  sound_enabled: 'soundEnabled',
  notifications_enabled: 'notificationsEnabled',
  show_toast_duration: 'showToastDuration',
  developer_mode: 'developerMode',
  experimental_features: 'experimentalFeatures',
  auto_save_enabled: 'autoSaveEnabled',
  auto_save_interval: 'autoSaveInterval',
}

// Reverse map: camelCase to snake_case
const REVERSE_SYNC_KEY_MAP: Record<string, string> = Object.fromEntries(
  Object.entries(SYNC_KEY_MAP).map(([k, v]) => [v, k])
)

interface UserPreferencesProviderProps {
  children: ReactNode
}

export function UserPreferencesProvider({ children }: UserPreferencesProviderProps) {
  const [preferences, setPreferencesState] = useLocalStorage<UserPreferences>(
    STORAGE_KEY,
    defaultPreferences
  )
  const settingsSync = useOptionalSettingsSync()

  const setPreference = useCallback(<K extends keyof UserPreferences>(
    key: K,
    value: UserPreferences[K]
  ) => {
    setPreferencesState(prev => ({
      ...prev,
      [key]: value,
    }))
    // Sync to DB
    const dbKey = REVERSE_SYNC_KEY_MAP[key as string]
    if (dbKey && settingsSync) {
      settingsSync.syncPreference({ [dbKey]: value } as Partial<UserPreferencesSync>)
    }
  }, [setPreferencesState, settingsSync])

  const setPreferences = useCallback((updates: Partial<UserPreferences>) => {
    setPreferencesState(prev => ({
      ...prev,
      ...updates,
    }))
    // Sync all updated keys to DB
    if (settingsSync) {
      const dbUpdates: Partial<UserPreferencesSync> = {}
      for (const [key, value] of Object.entries(updates)) {
        const dbKey = REVERSE_SYNC_KEY_MAP[key]
        if (dbKey) {
          Object.assign(dbUpdates, { [dbKey]: value })
        }
      }
      if (Object.keys(dbUpdates).length > 0) {
        settingsSync.syncPreference(dbUpdates)
      }
    }
  }, [setPreferencesState, settingsSync])

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

  // Apply preferences from DB sync (no DB write back to avoid loop)
  const applyFromSync = useCallback((syncPrefs: UserPreferencesSync) => {
    setPreferencesState(prev => {
      const updated = { ...prev }
      for (const [dbKey, localKey] of Object.entries(SYNC_KEY_MAP)) {
        const value = syncPrefs[dbKey as keyof UserPreferencesSync]
        if (value !== null && value !== undefined) {
          Object.assign(updated, { [localKey]: value })
        }
      }
      return updated
    })
  }, [setPreferencesState])

  // Listen for settings synced from DB
  useEffect(() => {
    const handler = (e: Event) => {
      const prefs = (e as CustomEvent<UserPreferencesSync>).detail
      if (prefs) {
        applyFromSync(prefs)
      }
    }
    window.addEventListener(SETTINGS_SYNCED_EVENT, handler)
    return () => window.removeEventListener(SETTINGS_SYNCED_EVENT, handler)
  }, [applyFromSync])

  const value = useMemo(() => ({
    preferences,
    setPreference,
    setPreferences,
    resetPreferences,
    resetPreference,
    isDefault,
    applyFromSync,
  }), [preferences, setPreference, setPreferences, resetPreferences, resetPreference, isDefault, applyFromSync])

  return (
    <UserPreferencesContext.Provider value={value}>
      {children}
    </UserPreferencesContext.Provider>
  )
}

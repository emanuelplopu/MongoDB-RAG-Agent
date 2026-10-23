/**
 * SettingsSyncContext - Coordinates settings sync between localStorage and DB.
 *
 * When preferences are pulled from DB (on login), dispatches a 'settings:synced'
 * custom event. Individual context providers listen for this and apply the relevant settings.
 *
 * The provider lives in ./SettingsSyncProvider.tsx.
 */

import { createContext, useContext } from 'react'
import { UserPreferencesSync } from '../api/client'

// Custom event for when preferences are pulled from DB
export const SETTINGS_SYNCED_EVENT = 'settings:synced'

export interface SettingsSyncContextType {
  /** Sync a partial preference update to both localStorage and DB */
  syncPreference: (updates: Partial<UserPreferencesSync>) => void
  /** Pull all preferences from DB and apply them */
  pullAndApply: () => Promise<UserPreferencesSync | null>
  /** Clear synced data (call on logout) */
  clearSync: () => void
}

export const SettingsSyncContext = createContext<SettingsSyncContextType | null>(null)

export function useSettingsSyncContext() {
  const context = useContext(SettingsSyncContext)
  if (!context) {
    throw new Error('useSettingsSyncContext must be used within SettingsSyncProvider')
  }
  return context
}

// Optional access - returns null if not within provider
export function useOptionalSettingsSync() {
  return useContext(SettingsSyncContext)
}

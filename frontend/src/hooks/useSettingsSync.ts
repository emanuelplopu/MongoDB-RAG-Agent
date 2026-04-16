/**
 * useSettingsSync - Syncs user preferences between localStorage and the backend DB.
 * 
 * Strategy:
 * - On login: pull preferences from DB, merge into localStorage (DB wins for first load)
 * - On preference change: write to localStorage immediately, debounce push to DB
 * - localStorage is the primary read source to offload DB for typical requests
 * - DB is the source of truth for cross-device/cross-session persistence
 */

import { useCallback, useRef, useEffect } from 'react'
import { authApi, UserPreferencesSync } from '../api/client'

const SYNC_DEBOUNCE_MS = 2000 // 2s debounce for DB writes
const SYNC_STORAGE_KEY = 'recallhub_synced_preferences'
const SYNC_VERSION_KEY = 'recallhub_preferences_version'

/**
 * Read synced preferences from localStorage cache
 */
export function getSyncedPreferences(): UserPreferencesSync | null {
  try {
    const raw = localStorage.getItem(SYNC_STORAGE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

/**
 * Write synced preferences to localStorage cache
 */
function setSyncedPreferences(prefs: UserPreferencesSync) {
  try {
    localStorage.setItem(SYNC_STORAGE_KEY, JSON.stringify(prefs))
    // Bump version to signal other contexts
    const version = Date.now().toString()
    localStorage.setItem(SYNC_VERSION_KEY, version)
  } catch (e) {
    console.warn('Failed to save synced preferences to localStorage', e)
  }
}

/**
 * Clear synced preferences (on logout)
 */
export function clearSyncedPreferences() {
  try {
    localStorage.removeItem(SYNC_STORAGE_KEY)
    localStorage.removeItem(SYNC_VERSION_KEY)
  } catch {
    // ignore
  }
}

/**
 * Hook providing debounced preference sync to the backend.
 * Returns functions to pull from DB, push to DB, and update individual fields.
 */
export function useSettingsSync() {
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout>>()
  const pendingUpdatesRef = useRef<UserPreferencesSync>({})
  const isMountedRef = useRef(true)

  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
      // Flush any pending updates before unmount
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
        flushToDb()
      }
    }
  }, [])

  /**
   * Pull preferences from DB and merge into localStorage.
   * Returns the merged preferences so callers can apply them.
   */
  const pullFromDb = useCallback(async (): Promise<UserPreferencesSync | null> => {
    try {
      const dbPrefs = await authApi.getPreferences()
      // Merge with any existing local prefs (DB wins for non-null values)
      const localPrefs = getSyncedPreferences() || {}
      const merged: UserPreferencesSync = { ...localPrefs }

      // DB values override local for all non-null fields
      for (const [key, value] of Object.entries(dbPrefs)) {
        if (value !== null && value !== undefined) {
          (merged as any)[key] = value
        }
      }

      setSyncedPreferences(merged)
      return merged
    } catch (e) {
      console.warn('Failed to pull preferences from DB', e)
      return getSyncedPreferences()
    }
  }, [])

  /**
   * Immediately flush pending updates to DB
   */
  const flushToDb = useCallback(async () => {
    const updates = { ...pendingUpdatesRef.current }
    if (Object.keys(updates).length === 0) return

    pendingUpdatesRef.current = {}
    try {
      const result = await authApi.updatePreferences(updates)
      if (result.preferences) {
        setSyncedPreferences(result.preferences as UserPreferencesSync)
      }
    } catch (e) {
      console.warn('Failed to sync preferences to DB', e)
      // Re-queue failed updates
      pendingUpdatesRef.current = { ...updates, ...pendingUpdatesRef.current }
    }
  }, [])

  /**
   * Queue a partial preference update. Writes to localStorage immediately,
   * debounces the DB write.
   */
  const syncPreference = useCallback((updates: Partial<UserPreferencesSync>) => {
    // Write to localStorage cache immediately
    const current = getSyncedPreferences() || {}
    const merged = { ...current, ...updates }
    setSyncedPreferences(merged)

    // Accumulate pending DB updates
    pendingUpdatesRef.current = { ...pendingUpdatesRef.current, ...updates }

    // Debounce DB write
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current)
    }
    debounceTimerRef.current = setTimeout(() => {
      if (isMountedRef.current) {
        flushToDb()
      }
    }, SYNC_DEBOUNCE_MS)
  }, [flushToDb])

  return {
    pullFromDb,
    syncPreference,
    flushToDb,
    getSyncedPreferences,
  }
}

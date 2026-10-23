import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowPathIcon,
  ArrowUturnLeftIcon,
  CheckIcon,
  ChevronRightIcon,
  FolderIcon,
  HomeIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline'
import { profilesApi, FolderBrowseResponse } from '../api/client'

interface ServerFolderPickerProps {
  /** Called with the absolute container path of the chosen folder. */
  onSelect: (path: string) => void
  onClose: () => void
  /** Folder to open first; the document roots when omitted. */
  initialPath?: string | null
}

/**
 * Modal that browses the server's document roots (`/app/mounts`, ...) so a
 * profile folder can be picked instead of typed. Lists directories only, one
 * level at a time, through `GET /profiles/folders/browse`.
 */
export default function ServerFolderPicker({ onSelect, onClose, initialPath }: ServerFolderPickerProps) {
  const { t } = useTranslation()
  const [listing, setListing] = useState<FolderBrowseResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // `t` is read through a ref so `load` stays stable even when the translation
  // hook hands out a new function per render (test mocks do); otherwise the
  // initial-load effect below would fire on every render.
  const tRef = useRef(t)
  tRef.current = t

  const load = useCallback(async (path: string | null) => {
    setIsLoading(true)
    setError(null)
    try {
      setListing(await profilesApi.browseFolders(path))
    } catch (err) {
      console.error('Error browsing server folders:', err)
      const message = err instanceof Error && err.message ? err.message : ''
      setError(message || tRef.current('profiles.browseFailed', 'Could not list the folder'))
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    load(initialPath ?? null)
  }, [initialPath, load])

  const currentPath = listing?.path ?? null
  const atRoots = currentPath === null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="server-folder-picker-title"
    >
      <div className="w-full max-w-xl rounded-2xl bg-surface dark:bg-gray-800 shadow-elevation-3 flex flex-col max-h-[80vh]">
        <div className="flex items-start justify-between gap-3 p-5 border-b border-surface-variant dark:border-gray-700">
          <div>
            <h3 id="server-folder-picker-title" className="text-lg font-medium text-primary-900 dark:text-gray-200">
              {t('profiles.folderPickerTitle', 'Choose a document folder')}
            </h3>
            <p className="text-xs text-secondary dark:text-gray-400 mt-1">
              {t('profiles.folderPickerHint')}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-secondary hover:bg-surface-variant dark:hover:bg-gray-700"
            aria-label={t('common.close', 'Close')}
          >
            <XMarkIcon className="h-5 w-5" />
          </button>
        </div>

        <div className="flex items-center gap-2 px-5 py-3 border-b border-surface-variant dark:border-gray-700 text-sm">
          <button
            type="button"
            onClick={() => load(null)}
            disabled={atRoots || isLoading}
            className="flex items-center gap-1 rounded-lg px-2 py-1 text-primary-700 dark:text-primary-300 hover:bg-surface-variant dark:hover:bg-gray-700 disabled:opacity-50"
            title={t('profiles.folderRoots', 'Document roots')}
          >
            <HomeIcon className="h-4 w-4" />
            {t('profiles.folderRoots', 'Document roots')}
          </button>
          <button
            type="button"
            onClick={() => load(listing?.parent ?? null)}
            disabled={atRoots || isLoading}
            className="flex items-center gap-1 rounded-lg px-2 py-1 text-primary-700 dark:text-primary-300 hover:bg-surface-variant dark:hover:bg-gray-700 disabled:opacity-50"
          >
            <ArrowUturnLeftIcon className="h-4 w-4" />
            {t('profiles.folderUp', 'Up')}
          </button>
          <code className="ml-auto truncate text-xs text-secondary dark:text-gray-400" title={currentPath ?? ''}>
            {currentPath ?? ''}
          </code>
        </div>

        <div className="flex-1 overflow-y-auto p-2 min-h-48">
          {isLoading ? (
            <div className="flex items-center justify-center py-10">
              <ArrowPathIcon className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : error ? (
            <div className="m-3 rounded-xl bg-red-50 dark:bg-red-900/30 p-3 text-sm text-red-700 dark:text-red-400">{error}</div>
          ) : listing && listing.entries.length === 0 ? (
            <p className="p-4 text-sm text-secondary dark:text-gray-400">{t('profiles.noSubfolders', 'No sub-folders')}</p>
          ) : (
            <ul className="space-y-1">
              {listing?.entries.map((entry) => (
                <li key={entry.path}>
                  <button
                    type="button"
                    onClick={() => load(entry.path)}
                    disabled={!entry.exists}
                    className="w-full flex items-center gap-3 rounded-lg px-3 py-2 text-left text-sm text-primary-900 dark:text-gray-200 hover:bg-surface-variant dark:hover:bg-gray-700 disabled:opacity-50"
                  >
                    <FolderIcon className="h-5 w-5 text-secondary dark:text-gray-400 shrink-0" />
                    <span className="truncate flex-1" title={entry.path}>{entry.name}</span>
                    {!entry.exists && (
                      <span className="text-xs text-red-600 dark:text-red-400">{t('profiles.folderNotMounted', 'not mounted')}</span>
                    )}
                    <ChevronRightIcon className="h-4 w-4 text-secondary dark:text-gray-500 shrink-0" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {listing?.truncated && (
            <p className="px-4 py-2 text-xs text-secondary dark:text-gray-400">
              {t('profiles.folderListTruncated', { count: listing.entries.length })}
            </p>
          )}
        </div>

        <div className="flex justify-end gap-2 p-4 border-t border-surface-variant dark:border-gray-700">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-secondary dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600"
          >
            {t('common.cancel', 'Cancel')}
          </button>
          <button
            type="button"
            onClick={() => currentPath && onSelect(currentPath)}
            disabled={atRoots || isLoading || !!error}
            className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-50"
          >
            <CheckIcon className="h-4 w-4" />
            {t('profiles.selectFolder', 'Use this folder')}
          </button>
        </div>
      </div>
    </div>
  )
}

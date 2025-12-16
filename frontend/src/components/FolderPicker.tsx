import { useState, useEffect, useCallback } from 'react'
import {
  FolderIcon,
  DocumentIcon,
  ChevronRightIcon,
  ArrowPathIcon,
  XMarkIcon,
  CheckIcon,
  HomeIcon,
} from '@heroicons/react/24/outline'
import {
  cloudSourcesApi,
  RemoteFolder,
  RemoteFile,
} from '../api/client'

interface FolderPickerProps {
  connectionId: string
  onSelect: (folder: RemoteFolder) => void
  onClose: () => void
  initialFolderId?: string
  allowMultiple?: boolean
  showFiles?: boolean
}

interface BreadcrumbItem {
  id: string
  name: string
  path: string
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
}

function formatDate(dateString?: string): string {
  if (!dateString) return ''
  return new Date(dateString).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

export default function FolderPicker({
  connectionId,
  onSelect,
  onClose,
  initialFolderId,
  allowMultiple = false,
  showFiles = false,
}: FolderPickerProps) {
  const [currentFolder, setCurrentFolder] = useState<RemoteFolder | null>(null)
  const [folders, setFolders] = useState<RemoteFolder[]>([])
  const [files, setFiles] = useState<RemoteFile[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [breadcrumbs, setBreadcrumbs] = useState<BreadcrumbItem[]>([])
  const [selectedFolders, setSelectedFolders] = useState<RemoteFolder[]>([])
  const [hasMore, setHasMore] = useState(false)

  const fetchFolder = useCallback(
    async (folderId?: string) => {
      setIsLoading(true)
      setError(null)
      try {
        const result = await cloudSourcesApi.browseFolder(connectionId, {
          folder_id: folderId,
        })
        setCurrentFolder(result.current_folder)
        setFolders(result.folders)
        setFiles(result.files)
        setHasMore(result.has_more)

        // Update breadcrumbs
        if (result.current_folder.path === '/' || !result.current_folder.path) {
          setBreadcrumbs([])
        } else {
          // Build breadcrumbs from path
          const parts = result.current_folder.path.split('/').filter(Boolean)
          const crumbs: BreadcrumbItem[] = []
          let currentPath = ''
          
          for (let i = 0; i < parts.length; i++) {
            currentPath += '/' + parts[i]
            crumbs.push({
              id: i === parts.length - 1 ? result.current_folder.id : '',
              name: parts[i],
              path: currentPath,
            })
          }
          setBreadcrumbs(crumbs)
        }
      } catch (err: any) {
        setError(err.message || 'Failed to load folder contents')
      } finally {
        setIsLoading(false)
      }
    },
    [connectionId]
  )

  useEffect(() => {
    fetchFolder(initialFolderId)
  }, [initialFolderId, fetchFolder])

  const handleFolderClick = (folder: RemoteFolder) => {
    fetchFolder(folder.id)
  }

  const handleFolderSelect = (folder: RemoteFolder) => {
    if (allowMultiple) {
      const isSelected = selectedFolders.some((f) => f.id === folder.id)
      if (isSelected) {
        setSelectedFolders(selectedFolders.filter((f) => f.id !== folder.id))
      } else {
        setSelectedFolders([...selectedFolders, folder])
      }
    } else {
      onSelect(folder)
    }
  }

  const handleSelectCurrent = () => {
    if (currentFolder) {
      onSelect(currentFolder)
    }
  }

  const handleConfirmMultiple = () => {
    if (selectedFolders.length > 0) {
      selectedFolders.forEach((folder) => onSelect(folder))
    }
  }

  const navigateToRoot = () => {
    fetchFolder(undefined)
  }

  const navigateToBreadcrumb = (crumb: BreadcrumbItem, index: number) => {
    // For now, we navigate by rebuilding the path
    // In a more complete implementation, we'd store folder IDs in breadcrumbs
    if (index === breadcrumbs.length - 1) return // Already here
    
    // Go back to root and navigate through
    // This is a simplified approach - ideally we'd store IDs
    if (crumb.id) {
      fetchFolder(crumb.id)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-[100] p-4">
      <div className="bg-surface dark:bg-gray-800 rounded-2xl shadow-xl max-w-3xl w-full max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-primary-900 dark:text-gray-100">
            Select Folder
          </h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
          >
            <XMarkIcon className="h-6 w-6" />
          </button>
        </div>

        {/* Breadcrumbs */}
        <div className="px-4 py-2 border-b border-gray-100 dark:border-gray-700 flex items-center gap-1 overflow-x-auto">
          <button
            onClick={navigateToRoot}
            className="flex items-center gap-1 px-2 py-1 rounded-lg text-sm text-secondary dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"
          >
            <HomeIcon className="h-4 w-4" />
            Root
          </button>
          {breadcrumbs.map((crumb, index) => (
            <div key={index} className="flex items-center">
              <ChevronRightIcon className="h-4 w-4 text-gray-400" />
              <button
                onClick={() => navigateToBreadcrumb(crumb, index)}
                className={`px-2 py-1 rounded-lg text-sm ${
                  index === breadcrumbs.length - 1
                    ? 'text-primary-900 dark:text-gray-100 font-medium'
                    : 'text-secondary dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700'
                }`}
              >
                {crumb.name}
              </button>
            </div>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto p-4">
          {error && (
            <div className="rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 p-4 mb-4">
              <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
            </div>
          )}

          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
            </div>
          ) : (
            <div className="space-y-1">
              {/* Folders */}
              {folders.length === 0 && files.length === 0 ? (
                <div className="text-center py-8">
                  <FolderIcon className="h-12 w-12 text-gray-300 dark:text-gray-600 mx-auto mb-3" />
                  <p className="text-secondary dark:text-gray-400">This folder is empty</p>
                </div>
              ) : (
                <>
                  {folders.map((folder) => {
                    const isSelected = selectedFolders.some((f) => f.id === folder.id)
                    return (
                      <div
                        key={folder.id}
                        className={`flex items-center gap-3 px-4 py-3 rounded-xl transition-colors ${
                          isSelected
                            ? 'bg-primary-100 dark:bg-primary-900/50 border border-primary'
                            : 'hover:bg-gray-50 dark:hover:bg-gray-700 border border-transparent'
                        }`}
                      >
                        {/* Selection checkbox for multiple mode */}
                        {allowMultiple && (
                          <button
                            onClick={() => handleFolderSelect(folder)}
                            className={`flex-shrink-0 w-5 h-5 rounded border ${
                              isSelected
                                ? 'bg-primary border-primary text-white'
                                : 'border-gray-300 dark:border-gray-600'
                            } flex items-center justify-center`}
                          >
                            {isSelected && <CheckIcon className="h-3 w-3" />}
                          </button>
                        )}

                        {/* Folder icon - click to navigate */}
                        <button
                          onClick={() => handleFolderClick(folder)}
                          className="flex items-center gap-3 flex-1 text-left"
                        >
                          <FolderIcon className="h-6 w-6 text-amber-500" />
                          <div className="flex-1 min-w-0">
                            <p className="font-medium text-primary-900 dark:text-gray-100 truncate">
                              {folder.name}
                            </p>
                            {folder.children_count !== undefined && (
                              <p className="text-xs text-secondary dark:text-gray-400">
                                {folder.children_count} items
                              </p>
                            )}
                          </div>
                          {folder.modified_at && (
                            <span className="text-xs text-secondary dark:text-gray-400">
                              {formatDate(folder.modified_at)}
                            </span>
                          )}
                          <ChevronRightIcon className="h-5 w-5 text-gray-400 flex-shrink-0" />
                        </button>

                        {/* Select button for single mode */}
                        {!allowMultiple && (
                          <button
                            onClick={() => handleFolderSelect(folder)}
                            className="flex-shrink-0 px-3 py-1.5 rounded-lg text-sm bg-primary text-white hover:bg-primary-700"
                          >
                            Select
                          </button>
                        )}
                      </div>
                    )
                  })}

                  {/* Files (optional display) */}
                  {showFiles &&
                    files.map((file) => (
                      <div
                        key={file.id}
                        className="flex items-center gap-3 px-4 py-3 rounded-xl hover:bg-gray-50 dark:hover:bg-gray-700"
                      >
                        <DocumentIcon className="h-6 w-6 text-gray-400" />
                        <div className="flex-1 min-w-0">
                          <p className="font-medium text-primary-900 dark:text-gray-100 truncate">
                            {file.name}
                          </p>
                          <p className="text-xs text-secondary dark:text-gray-400">
                            {formatBytes(file.size_bytes)} • {file.mime_type.split('/').pop()}
                          </p>
                        </div>
                        <span className="text-xs text-secondary dark:text-gray-400">
                          {formatDate(file.modified_at)}
                        </span>
                      </div>
                    ))}
                </>
              )}

              {hasMore && (
                <div className="text-center py-4">
                  <p className="text-sm text-secondary dark:text-gray-400">
                    More items available. Navigate into subfolders to see them.
                  </p>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div className="text-sm text-secondary dark:text-gray-400">
            {allowMultiple && selectedFolders.length > 0 && (
              <span>{selectedFolders.length} folder(s) selected</span>
            )}
            {!allowMultiple && currentFolder && (
              <span>Current: {currentFolder.name || 'Root'}</span>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 text-primary-900 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              Cancel
            </button>
            {allowMultiple ? (
              <button
                onClick={handleConfirmMultiple}
                disabled={selectedFolders.length === 0}
                className="px-4 py-2 rounded-xl bg-primary text-white hover:bg-primary-700 disabled:opacity-50"
              >
                Add Selected ({selectedFolders.length})
              </button>
            ) : (
              <button
                onClick={handleSelectCurrent}
                disabled={!currentFolder}
                className="px-4 py-2 rounded-xl bg-primary text-white hover:bg-primary-700 disabled:opacity-50"
              >
                Select Current Folder
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

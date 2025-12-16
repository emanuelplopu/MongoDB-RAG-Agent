import { useState, useEffect, useMemo, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { Tree, NodeRendererProps } from 'react-arborist'
import {
  DocumentTextIcon,
  TrashIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ArrowPathIcon,
  FolderIcon,
  FolderOpenIcon,
  MagnifyingGlassIcon,
  Squares2X2Icon,
  ListBulletIcon,
  ChevronRightIcon as BreadcrumbIcon,
  HomeIcon,
  PhotoIcon,
  MusicalNoteIcon,
  FilmIcon,
  DocumentIcon,
  XMarkIcon,
  ArrowUpIcon,
  ChevronUpIcon,
  ChevronDownIcon,
  WrenchScrewdriverIcon,
} from '@heroicons/react/24/outline'
import { documentsApi, ingestionApi, Document, DocumentListResponse, FolderInfo, FoldersResponse, SortField, SortOrder, MetadataRebuildStatus } from '../api/client'

// Types for folder tree
interface TreeNode {
  id: string
  name: string
  children?: TreeNode[]
  isFolder: boolean
  documentCount?: number
}

// Get file icon based on extension
const getFileIcon = (filename: string) => {
  const ext = filename.split('.').pop()?.toLowerCase() || ''
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp'].includes(ext)) {
    return <PhotoIcon className="h-5 w-5 text-pink-500" />
  }
  if (['mp3', 'wav', 'flac', 'm4a', 'ogg'].includes(ext)) {
    return <MusicalNoteIcon className="h-5 w-5 text-purple-500" />
  }
  if (['mp4', 'avi', 'mkv', 'mov', 'webm'].includes(ext)) {
    return <FilmIcon className="h-5 w-5 text-blue-500" />
  }
  if (['pdf'].includes(ext)) {
    return <DocumentIcon className="h-5 w-5 text-red-500" />
  }
  if (['doc', 'docx'].includes(ext)) {
    return <DocumentIcon className="h-5 w-5 text-blue-600" />
  }
  if (['xls', 'xlsx'].includes(ext)) {
    return <DocumentIcon className="h-5 w-5 text-green-600" />
  }
  if (['ppt', 'pptx'].includes(ext)) {
    return <DocumentIcon className="h-5 w-5 text-orange-500" />
  }
  if (['md', 'txt'].includes(ext)) {
    return <DocumentTextIcon className="h-5 w-5 text-gray-500" />
  }
  return <DocumentTextIcon className="h-5 w-5 text-primary" />
}

// Build folder tree from folder info response (from backend)
const buildFolderTreeFromFolders = (folders: FolderInfo[]): TreeNode[] => {
  if (!folders || folders.length === 0) return []
  
  // Group folders by depth and parent
  const nodeMap: Map<string, TreeNode> = new Map()
  
  // First pass: create all nodes
  folders.forEach(folder => {
    nodeMap.set(folder.path, {
      id: folder.path,
      name: folder.name,
      isFolder: true,
      documentCount: folder.count,
      children: []
    })
  })
  
  // Second pass: build hierarchy
  const rootNodes: TreeNode[] = []
  
  folders.forEach(folder => {
    const node = nodeMap.get(folder.path)!
    
    // Find parent path
    const lastSlash = folder.path.lastIndexOf('/')
    if (lastSlash === -1) {
      // This is a root folder
      rootNodes.push(node)
    } else {
      const parentPath = folder.path.substring(0, lastSlash)
      const parent = nodeMap.get(parentPath)
      if (parent) {
        parent.children = parent.children || []
        parent.children.push(node)
      } else {
        // Parent doesn't exist, treat as root
        rootNodes.push(node)
      }
    }
  })
  
  // Sort children alphabetically
  const sortChildren = (nodes: TreeNode[]): TreeNode[] => {
    return nodes.sort((a, b) => a.name.localeCompare(b.name)).map(node => ({
      ...node,
      children: node.children && node.children.length > 0 
        ? sortChildren(node.children) 
        : undefined
    }))
  }
  
  return sortChildren(rootNodes)
}

// Custom tree node renderer
function FolderNode({ node, style, dragHandle }: NodeRendererProps<TreeNode>) {
  return (
    <div
      ref={dragHandle}
      style={style}
      className={`flex items-center gap-2 py-1.5 px-2 rounded-lg cursor-pointer transition-colors
        ${node.isSelected ? 'bg-primary-100 dark:bg-primary-900/40' : 'hover:bg-surface-variant dark:hover:bg-gray-700'}`}
      onClick={() => node.select()}
    >
      <span
        className="flex-shrink-0 cursor-pointer"
        onClick={(e) => {
          e.stopPropagation()
          node.toggle()
        }}
      >
        {node.isOpen ? (
          <FolderOpenIcon className="h-5 w-5 text-yellow-500" />
        ) : (
          <FolderIcon className="h-5 w-5 text-yellow-500" />
        )}
      </span>
      <span className="text-sm text-primary-900 dark:text-gray-200 truncate flex-1">
        {node.data.name}
      </span>
      {node.data.documentCount !== undefined && node.data.documentCount > 0 && (
        <span className="text-xs text-secondary dark:text-gray-500 bg-surface-variant dark:bg-gray-700 px-1.5 py-0.5 rounded">
          {node.data.documentCount}
        </span>
      )}
    </div>
  )
}

export default function DocumentsPage() {
  const [allDocuments, setAllDocuments] = useState<Document[]>([])
  const [folderData, setFolderData] = useState<FoldersResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isFoldersLoading, setIsFoldersLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(50)
  const [totalPages, setTotalPages] = useState(1)
  const [total, setTotal] = useState(0)
  
  // Explorer state
  const [currentPath, setCurrentPath] = useState<string>('')
  const [searchInput, setSearchInput] = useState('')
  const [searchTerm, setSearchTerm] = useState('')  // Debounced search term
  const [viewMode, setViewMode] = useState<'list' | 'grid'>('list')
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [sortBy, setSortBy] = useState<SortField>('modified')
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc')
  
  // Metadata rebuild state
  const [rebuildStatus, setRebuildStatus] = useState<MetadataRebuildStatus | null>(null)
  const [isRebuilding, setIsRebuilding] = useState(false)

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearchTerm(searchInput)
    }, 300) // 300ms debounce
    return () => clearTimeout(timer)
  }, [searchInput])

  // Fetch folder structure (once, for tree building)
  const fetchFolders = useCallback(async () => {
    setIsFoldersLoading(true)
    try {
      const response = await documentsApi.getFolders()
      setFolderData(response)
      if (response.total_documents !== undefined) {
        setTotal(response.total_documents)
      }
    } catch (err) {
      console.error('Error fetching folders:', err)
      // Non-critical error - tree will just be empty
    } finally {
      setIsFoldersLoading(false)
    }
  }, [])

  // Fetch documents for current page with folder and search filters
  const fetchDocuments = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      // When searching, use recursive mode. When browsing folders, use exact mode.
      const useExactFolder = !searchTerm.trim()
      const response: DocumentListResponse = await documentsApi.list(
        page, 
        pageSize, 
        currentPath || undefined,
        searchTerm.trim() || undefined,
        useExactFolder,
        sortBy,
        sortOrder
      )
      setAllDocuments(response.documents)
      setTotalPages(response.total_pages)
      setTotal(response.total)
    } catch (err) {
      console.error('Error fetching documents:', err)
      setError('Failed to load documents.')
    } finally {
      setIsLoading(false)
    }
  }, [page, pageSize, currentPath, searchTerm, sortBy, sortOrder])

  useEffect(() => {
    fetchFolders()
  }, [])

  useEffect(() => {
    fetchDocuments()
  }, [fetchDocuments])

  // Build folder tree from backend response
  const folderTree = useMemo(() => {
    if (!folderData || !folderData.folders) return []
    return buildFolderTreeFromFolders(folderData.folders)
  }, [folderData])

  // Documents are now server-side filtered, use directly
  const filteredDocuments = allDocuments

  // Get subfolders of current path from folder data
  const currentSubfolders = useMemo(() => {
    if (!folderData || !folderData.folders || searchTerm.trim()) return []
    
    const normalizedCurrentPath = currentPath.replace(/\\/g, '/')
    
    // Find folders that are direct children of current path
    return folderData.folders.filter(folder => {
      const folderPath = folder.path
      
      if (!currentPath) {
        // Root level - folders with depth 0
        return folder.depth === 0
      }
      
      // Check if this folder is a direct child of current path
      if (!folderPath.startsWith(normalizedCurrentPath + '/')) return false
      
      // Get the remaining path after current path
      const remainingPath = folderPath.substring(normalizedCurrentPath.length + 1)
      
      // Direct child has no more slashes
      return !remainingPath.includes('/')
    })
  }, [folderData, currentPath, searchTerm])

  // Navigate to parent folder
  const goToParent = () => {
    if (!currentPath) return
    const lastSlash = currentPath.lastIndexOf('/')
    if (lastSlash === -1) {
      setCurrentPath('')
    } else {
      setCurrentPath(currentPath.substring(0, lastSlash))
    }
  }

  // Breadcrumb parts
  const breadcrumbs = useMemo(() => {
    if (!currentPath) return []
    return currentPath.split('/').filter(Boolean)
  }, [currentPath])

  const handleDelete = async (documentId: string, e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!confirm('Are you sure you want to delete this document and all its chunks?')) {
      return
    }
    try {
      await documentsApi.delete(documentId)
      fetchDocuments()
    } catch (err) {
      console.error('Error deleting document:', err)
      setError('Failed to delete document.')
    }
  }

  const formatDate = (dateStr?: string) => {
    if (!dateStr) return 'N/A'
    try {
      const date = new Date(dateStr)
      if (isNaN(date.getTime())) return 'N/A'
      return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      })
    } catch {
      return 'N/A'
    }
  }

  const formatSize = (chunks: number) => {
    return `${chunks} chunks`
  }

  // Reset page when filter changes (but not on initial load)
  const [initialLoad, setInitialLoad] = useState(true)
  useEffect(() => {
    if (initialLoad) {
      setInitialLoad(false)
      return
    }
    setPage(1)
  }, [currentPath, searchInput])

  // Handle folder selection from tree
  const handleFolderSelect = (nodes: any[]) => {
    if (nodes && nodes.length > 0 && nodes[0]?.id) {
      setCurrentPath(nodes[0].id)
    }
  }

  // Navigate breadcrumb
  const navigateToBreadcrumb = (index: number) => {
    if (index < 0) {
      setCurrentPath('')
    } else {
      setCurrentPath(breadcrumbs.slice(0, index + 1).join('/'))
    }
  }

  // Toggle sort - cycle through field or toggle order
  const handleSort = (field: SortField) => {
    if (sortBy === field) {
      // Toggle order
      setSortOrder(sortOrder === 'desc' ? 'asc' : 'desc')
    } else {
      // Change field, default to descending
      setSortBy(field)
      setSortOrder('desc')
    }
  }

  // Get sort icon for column header
  const getSortIcon = (field: SortField) => {
    if (sortBy !== field) return null
    return sortOrder === 'desc' 
      ? <ChevronDownIcon className="h-3 w-3 inline ml-1" />
      : <ChevronUpIcon className="h-3 w-3 inline ml-1" />
  }

  // Sort options for dropdown
  const sortOptions: { value: SortField; label: string }[] = [
    { value: 'modified', label: 'Date Modified' },
    { value: 'name', label: 'Name' },
    { value: 'size', label: 'Size' },
    { value: 'type', label: 'Type' },
  ]

  // Check for rebuild status on mount and poll while running
  useEffect(() => {
    const checkRebuildStatus = async () => {
      try {
        const response = await ingestionApi.getMetadataRebuildStatus()
        if (response.status) {
          setRebuildStatus(response.status)
          setIsRebuilding(response.running)
        }
      } catch (err) {
        console.error('Error checking rebuild status:', err)
      }
    }
    
    checkRebuildStatus()
    
    // Poll while rebuilding
    const interval = setInterval(() => {
      if (isRebuilding) {
        checkRebuildStatus()
      }
    }, 1000)
    
    return () => clearInterval(interval)
  }, [isRebuilding])

  // Start metadata rebuild
  const handleStartRebuild = async () => {
    try {
      const response = await ingestionApi.startMetadataRebuild()
      if (response.success) {
        setRebuildStatus(response.status)
        setIsRebuilding(true)
      } else {
        setError(response.message)
      }
    } catch (err) {
      console.error('Error starting rebuild:', err)
      setError('Failed to start metadata rebuild')
    }
  }

  // Count documents with 0 chunks (for showing rebuild button)
  const docsWithZeroChunks = useMemo(() => {
    return allDocuments.filter(d => d.chunks_count === 0).length
  }, [allDocuments])

  return (
    <div className="flex h-[calc(100vh-8rem)] gap-4">
      {/* Left Sidebar - Folder Tree */}
      {!sidebarCollapsed && (
        <div className="w-64 flex-shrink-0 flex flex-col bg-surface dark:bg-gray-800 rounded-2xl shadow-elevation-1 overflow-hidden">
          {/* Sidebar Header */}
          <div className="p-3 border-b border-surface-variant dark:border-gray-700 flex items-center justify-between">
            <h3 className="text-sm font-semibold text-primary-900 dark:text-gray-200">Folders</h3>
            <button
              onClick={() => setSidebarCollapsed(true)}
              className="p-1 rounded hover:bg-surface-variant dark:hover:bg-gray-700"
            >
              <ChevronLeftIcon className="h-4 w-4 text-secondary" />
            </button>
          </div>
          
          {/* Root folder */}
          <div
            onClick={() => setCurrentPath('')}
            className={`flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors
              ${!currentPath ? 'bg-primary-100 dark:bg-primary-900/40' : 'hover:bg-surface-variant dark:hover:bg-gray-700'}`}
          >
            <HomeIcon className="h-5 w-5 text-primary" />
            <span className="text-sm text-primary-900 dark:text-gray-200">All Documents</span>
            <span className="text-xs text-secondary dark:text-gray-500 ml-auto">{total}</span>
          </div>
          
          {/* Folder Tree */}
          <div className="flex-1 overflow-auto p-2" style={{ minHeight: 0 }}>
            {isFoldersLoading ? (
              <div className="flex items-center justify-center py-4">
                <ArrowPathIcon className="h-5 w-5 animate-spin text-secondary" />
              </div>
            ) : folderTree.length > 0 ? (
              <Tree
                data={folderTree}
                openByDefault={false}
                width={240}
                height={Math.max(200, Math.min(600, folderTree.length * 32))}
                indent={16}
                rowHeight={32}
                onSelect={handleFolderSelect}
                disableDrag
                disableDrop
              >
                {FolderNode}
              </Tree>
            ) : (
              <div className="text-center text-sm text-secondary dark:text-gray-500 py-4">
                No folders
              </div>
            )}
          </div>
        </div>
      )}

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-w-0 bg-surface dark:bg-gray-800 rounded-2xl shadow-elevation-1 overflow-hidden">
        {/* Toolbar */}
        <div className="p-3 border-b border-surface-variant dark:border-gray-700">
          <div className="flex items-center gap-3">
            {/* Collapse toggle */}
            {sidebarCollapsed && (
              <button
                onClick={() => setSidebarCollapsed(false)}
                className="p-2 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700"
                title="Show folders"
              >
                <ChevronRightIcon className="h-4 w-4 text-secondary" />
              </button>
            )}
            
            {/* Breadcrumbs */}
            <div className="flex items-center gap-1 text-sm flex-1 min-w-0 overflow-x-auto">
              <button
                onClick={() => navigateToBreadcrumb(-1)}
                className={`flex items-center gap-1 px-2 py-1 rounded hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors
                  ${!currentPath ? 'text-primary font-medium' : 'text-secondary dark:text-gray-400'}`}
              >
                <HomeIcon className="h-4 w-4" />
                <span>Documents</span>
              </button>
              {breadcrumbs.map((crumb, idx) => (
                <div key={idx} className="flex items-center">
                  <BreadcrumbIcon className="h-4 w-4 text-secondary dark:text-gray-500" />
                  <button
                    onClick={() => navigateToBreadcrumb(idx)}
                    className={`px-2 py-1 rounded hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors
                      ${idx === breadcrumbs.length - 1 ? 'text-primary font-medium' : 'text-secondary dark:text-gray-400'}`}
                  >
                    {crumb}
                  </button>
                </div>
              ))}
            </div>

            {/* Search */}
            <div className="relative w-64">
              <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-secondary" />
              <input
                type="text"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Search documents..."
                className="w-full pl-9 pr-8 py-2 text-sm bg-surface-variant dark:bg-gray-700 border-0 rounded-lg text-primary-900 dark:text-gray-200 placeholder:text-secondary focus:ring-2 focus:ring-primary"
              />
              {searchInput && (
                <button
                  onClick={() => setSearchInput('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1 hover:bg-surface dark:hover:bg-gray-600 rounded"
                >
                  <XMarkIcon className="h-4 w-4 text-secondary" />
                </button>
              )}
            </div>

            {/* Sort Dropdown */}
            <div className="flex items-center gap-1">
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as SortField)}
                className="text-sm bg-surface-variant dark:bg-gray-700 border-0 rounded-lg py-2 pl-3 pr-8 text-primary-900 dark:text-gray-200 focus:ring-2 focus:ring-primary cursor-pointer"
              >
                {sortOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <button
                onClick={() => setSortOrder(sortOrder === 'desc' ? 'asc' : 'desc')}
                className="p-2 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors"
                title={sortOrder === 'desc' ? 'Sort descending' : 'Sort ascending'}
              >
                {sortOrder === 'desc' 
                  ? <ChevronDownIcon className="h-4 w-4 text-secondary" />
                  : <ChevronUpIcon className="h-4 w-4 text-secondary" />}
              </button>
            </div>

            {/* View Mode Toggle */}
            <div className="flex items-center gap-1 bg-surface-variant dark:bg-gray-700 rounded-lg p-1">
              <button
                onClick={() => setViewMode('list')}
                className={`p-1.5 rounded transition-colors ${viewMode === 'list' ? 'bg-white dark:bg-gray-600 shadow-sm' : ''}`}
                title="List view"
              >
                <ListBulletIcon className="h-4 w-4 text-primary-900 dark:text-gray-200" />
              </button>
              <button
                onClick={() => setViewMode('grid')}
                className={`p-1.5 rounded transition-colors ${viewMode === 'grid' ? 'bg-white dark:bg-gray-600 shadow-sm' : ''}`}
                title="Grid view"
              >
                <Squares2X2Icon className="h-4 w-4 text-primary-900 dark:text-gray-200" />
              </button>
            </div>

            {/* Rebuild Metadata Button */}
            {(docsWithZeroChunks > 0 || isRebuilding) && (
              <button
                onClick={handleStartRebuild}
                disabled={isRebuilding}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg transition-colors ${
                  isRebuilding 
                    ? 'bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300' 
                    : 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300 hover:bg-yellow-200 dark:hover:bg-yellow-900/50'
                }`}
                title={isRebuilding ? 'Rebuilding metadata...' : `${docsWithZeroChunks} documents need metadata repair`}
              >
                <WrenchScrewdriverIcon className={`h-4 w-4 ${isRebuilding ? 'animate-spin' : ''}`} />
                {isRebuilding 
                  ? `Rebuilding... ${rebuildStatus?.progress_percent || 0}%`
                  : `Fix ${docsWithZeroChunks} docs`
                }
              </button>
            )}

            {/* Refresh */}
            <button
              onClick={() => { fetchFolders(); fetchDocuments(); }}
              disabled={isLoading || isFoldersLoading}
              className="p-2 rounded-lg hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors"
              title="Refresh"
            >
              <ArrowPathIcon className={`h-4 w-4 text-secondary ${(isLoading || isFoldersLoading) ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {/* Status bar */}
        <div className="px-3 py-2 bg-surface-variant/50 dark:bg-gray-700/50 text-xs text-secondary dark:text-gray-400 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span>
              {currentSubfolders.length > 0 && `${currentSubfolders.length} folder${currentSubfolders.length !== 1 ? 's' : ''}, `}
              {filteredDocuments.length} {filteredDocuments.length === 1 ? 'file' : 'files'}
              {searchTerm && ` matching "${searchTerm}"`}
              {currentPath && ` in ${currentPath}`}
            </span>
            {/* Rebuild progress bar */}
            {isRebuilding && rebuildStatus && (
              <div className="flex items-center gap-2">
                <div className="w-24 h-1.5 bg-gray-300 dark:bg-gray-600 rounded-full overflow-hidden">
                  <div 
                    className="h-full bg-primary transition-all duration-300"
                    style={{ width: `${rebuildStatus.progress_percent}%` }}
                  />
                </div>
                <span className="text-primary-600 dark:text-primary-400">
                  {rebuildStatus.processed}/{rebuildStatus.total} ({rebuildStatus.updated} updated)
                </span>
              </div>
            )}
            {/* Rebuild completed message */}
            {rebuildStatus?.status === 'completed' && !isRebuilding && (
              <span className="text-green-600 dark:text-green-400">
                Rebuild complete: {rebuildStatus.updated} docs updated
              </span>
            )}
          </div>
          <span>Page {page} of {totalPages}</span>
        </div>

        {/* Error */}
        {error && (
          <div className="mx-3 mt-3 rounded-xl bg-red-50 dark:bg-red-900/30 p-3 text-sm text-red-700 dark:text-red-400">
            {error}
          </div>
        )}

        {/* Content */}
        <div className="flex-1 overflow-auto p-3">
          {isLoading ? (
            <div className="flex items-center justify-center h-full">
              <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
            </div>
          ) : (currentSubfolders.length === 0 && filteredDocuments.length === 0) ? (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <DocumentTextIcon className="h-16 w-16 text-secondary dark:text-gray-600 mb-4" />
              <p className="text-secondary dark:text-gray-400 mb-1">
                {searchTerm ? 'No documents match your search' : 'No documents in this folder'}
              </p>
              <p className="text-sm text-secondary dark:text-gray-500">
                {searchTerm ? 'Try different keywords' : 'Use ingestion to add documents'}
              </p>
            </div>
          ) : viewMode === 'grid' ? (
            /* Grid View */
            <div className="space-y-4">
              {/* Go to parent folder */}
              {currentPath && !searchTerm && (
                <div
                  onClick={goToParent}
                  className="inline-flex items-center gap-2 px-3 py-2 rounded-lg cursor-pointer hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors"
                >
                  <ArrowUpIcon className="h-5 w-5 text-secondary" />
                  <span className="text-sm text-secondary dark:text-gray-400">..</span>
                </div>
              )}
              
              {/* Subfolders grid */}
              {currentSubfolders.length > 0 && (
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
                  {currentSubfolders.map((folder) => (
                    <div
                      key={folder.path}
                      onClick={() => setCurrentPath(folder.path)}
                      className="group flex flex-col items-center p-4 rounded-xl transition-all cursor-pointer hover:bg-surface-variant dark:hover:bg-gray-700"
                    >
                      <FolderIcon className="h-10 w-10 text-yellow-500" />
                      <span className="mt-2 text-xs text-center text-primary-900 dark:text-gray-200 line-clamp-2 w-full">
                        {folder.name}
                      </span>
                      <span className="text-[10px] text-secondary dark:text-gray-500">
                        {folder.count} items
                      </span>
                    </div>
                  ))}
                </div>
              )}
              
              {/* Documents grid */}
              {filteredDocuments.length > 0 && (
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
                  {filteredDocuments.map((doc) => (
                    <Link
                      key={doc.id}
                      to={`/documents/${doc.id}`}
                      className={`group flex flex-col items-center p-4 rounded-xl transition-all hover:bg-surface-variant dark:hover:bg-gray-700
                        ${selectedDocId === doc.id ? 'bg-primary-100 dark:bg-primary-900/40 ring-2 ring-primary' : ''}`}
                      onClick={(e) => {
                        if (e.detail === 1) {
                          e.preventDefault()
                          setSelectedDocId(doc.id)
                        }
                      }}
                      onDoubleClick={() => {}}
                    >
                      <div className="relative">
                        {getFileIcon(doc.source || doc.title)}
                        <div className="absolute -top-1 -right-1 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            onClick={(e) => handleDelete(doc.id, e)}
                            className="p-1 bg-red-100 dark:bg-red-900/50 rounded-full hover:bg-red-200 dark:hover:bg-red-900"
                          >
                            <TrashIcon className="h-3 w-3 text-red-600" />
                          </button>
                        </div>
                      </div>
                      <span className="mt-2 text-xs text-center text-primary-900 dark:text-gray-200 line-clamp-2 w-full">
                        {doc.title}
                      </span>
                      <span className="text-[10px] text-secondary dark:text-gray-500">
                        {doc.chunks_count} chunks
                      </span>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          ) : (
            /* List View */
            <table className="min-w-full">
              <thead className="sticky top-0 bg-surface dark:bg-gray-800">
                <tr className="border-b border-surface-variant dark:border-gray-700">
                  <th 
                    onClick={() => handleSort('name')}
                    className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wider text-secondary dark:text-gray-400 cursor-pointer hover:text-primary dark:hover:text-primary-300 select-none"
                  >
                    Name{getSortIcon('name')}
                  </th>
                  <th className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wider text-secondary dark:text-gray-400 hidden md:table-cell">
                    Path
                  </th>
                  <th 
                    onClick={() => handleSort('size')}
                    className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wider text-secondary dark:text-gray-400 w-24 cursor-pointer hover:text-primary dark:hover:text-primary-300 select-none"
                  >
                    Size{getSortIcon('size')}
                  </th>
                  <th 
                    onClick={() => handleSort('modified')}
                    className="px-3 py-2 text-left text-xs font-medium uppercase tracking-wider text-secondary dark:text-gray-400 w-32 hidden sm:table-cell cursor-pointer hover:text-primary dark:hover:text-primary-300 select-none"
                  >
                    Modified{getSortIcon('modified')}
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-medium uppercase tracking-wider text-secondary dark:text-gray-400 w-16">
                    
                  </th>
                </tr>
              </thead>
              <tbody>
                {/* Go to parent folder row */}
                {currentPath && !searchTerm && (
                  <tr
                    className="border-b border-surface-variant/50 dark:border-gray-700/50 transition-colors cursor-pointer hover:bg-surface-variant/50 dark:hover:bg-gray-700/50"
                    onClick={goToParent}
                  >
                    <td className="px-3 py-2" colSpan={5}>
                      <div className="flex items-center gap-2">
                        <ArrowUpIcon className="h-5 w-5 text-secondary" />
                        <span className="text-sm text-secondary dark:text-gray-400">..</span>
                      </div>
                    </td>
                  </tr>
                )}
                
                {/* Subfolder rows */}
                {currentSubfolders.map((folder) => (
                  <tr
                    key={folder.path}
                    className="border-b border-surface-variant/50 dark:border-gray-700/50 transition-colors cursor-pointer hover:bg-surface-variant/50 dark:hover:bg-gray-700/50"
                    onClick={() => setCurrentPath(folder.path)}
                    onDoubleClick={() => setCurrentPath(folder.path)}
                  >
                    <td className="px-3 py-2">
                      <div className="flex items-center gap-2">
                        <FolderIcon className="h-5 w-5 text-yellow-500" />
                        <span className="text-sm text-primary-900 dark:text-gray-200">
                          {folder.name}
                        </span>
                      </div>
                    </td>
                    <td className="px-3 py-2 text-sm text-secondary dark:text-gray-400 truncate max-w-xs hidden md:table-cell">
                      {folder.path}
                    </td>
                    <td className="px-3 py-2 text-sm text-secondary dark:text-gray-400">
                      {folder.count} items
                    </td>
                    <td className="px-3 py-2 text-sm text-secondary dark:text-gray-400 hidden sm:table-cell">
                      —
                    </td>
                    <td className="px-3 py-2"></td>
                  </tr>
                ))}
                
                {/* Document rows */}
                {filteredDocuments.map((doc) => (
                  <tr
                    key={doc.id}
                    className={`group border-b border-surface-variant/50 dark:border-gray-700/50 transition-colors cursor-pointer
                      ${selectedDocId === doc.id ? 'bg-primary-100 dark:bg-primary-900/40' : 'hover:bg-surface-variant/50 dark:hover:bg-gray-700/50'}`}
                    onClick={() => setSelectedDocId(doc.id)}
                    onDoubleClick={() => window.location.href = `/documents/${doc.id}`}
                  >
                    <td className="px-3 py-2">
                      <Link
                        to={`/documents/${doc.id}`}
                        className="flex items-center gap-2 group"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {getFileIcon(doc.source || doc.title)}
                        <span className="text-sm text-primary-900 dark:text-gray-200 group-hover:text-primary group-hover:underline truncate">
                          {doc.title}
                        </span>
                      </Link>
                    </td>
                    <td className="px-3 py-2 text-sm text-secondary dark:text-gray-400 truncate max-w-xs hidden md:table-cell">
                      {doc.source}
                    </td>
                    <td className="px-3 py-2 text-sm text-secondary dark:text-gray-400">
                      {formatSize(doc.chunks_count)}
                    </td>
                    <td className="px-3 py-2 text-sm text-secondary dark:text-gray-400 hidden sm:table-cell">
                      {formatDate(doc.created_at)}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <button
                        onClick={(e) => handleDelete(doc.id, e)}
                        className="p-1.5 rounded-lg text-red-500 hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors opacity-0 group-hover:opacity-100"
                        title="Delete"
                      >
                        <TrashIcon className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="px-3 py-2 border-t border-surface-variant dark:border-gray-700 flex items-center justify-end gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg bg-surface-variant dark:bg-gray-700 text-primary-700 dark:text-primary-300 disabled:opacity-50 hover:bg-primary-100 dark:hover:bg-gray-600 transition-colors"
            >
              <ChevronLeftIcon className="h-4 w-4" />
              Prev
            </button>
            <span className="text-sm text-secondary dark:text-gray-400 px-2">
              {page} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-lg bg-surface-variant dark:bg-gray-700 text-primary-700 dark:text-primary-300 disabled:opacity-50 hover:bg-primary-100 dark:hover:bg-gray-600 transition-colors"
            >
              Next
              <ChevronRightIcon className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

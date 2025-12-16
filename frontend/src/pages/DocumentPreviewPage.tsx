import { useState, useEffect } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  ArrowLeftIcon,
  FolderOpenIcon,
  DocumentIcon,
  EyeIcon,
  ClockIcon,
  DocumentTextIcon,
  CubeIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CloudIcon,
  ArrowTopRightOnSquareIcon,
} from '@heroicons/react/24/outline'
import { documentsApi, DocumentFullInfo, DocumentChunk, cloudSourcesApi } from '../api/client'

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 Bytes'
  const k = 1024
  const sizes = ['Bytes', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

function formatDate(dateStr?: string): string {
  if (!dateStr) return 'Unknown'
  return new Date(dateStr).toLocaleString()
}

function ChunkCard({ chunk, index }: { chunk: DocumentChunk; index: number }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="border border-surface-variant dark:border-gray-700 rounded-xl overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-3 bg-surface-variant/50 dark:bg-gray-800/50 hover:bg-surface-variant dark:hover:bg-gray-800 transition-colors"
      >
        <div className="flex items-center gap-3">
          {expanded ? (
            <ChevronDownIcon className="h-4 w-4 text-secondary" />
          ) : (
            <ChevronRightIcon className="h-4 w-4 text-secondary" />
          )}
          <span className="font-medium text-primary-900 dark:text-primary-200">
            Chunk {index + 1}
          </span>
          {chunk.token_count && (
            <span className="text-xs text-secondary bg-white dark:bg-gray-700 px-2 py-1 rounded-full">
              {chunk.token_count} tokens
            </span>
          )}
          {chunk.has_embedding && (
            <span className="text-xs text-green-700 dark:text-green-400 bg-green-100 dark:bg-green-900/30 px-2 py-1 rounded-full">
              Embedded ({chunk.embedding_dimensions}d)
            </span>
          )}
        </div>
      </button>
      {expanded && (
        <div className="p-4 bg-white dark:bg-gray-900">
          <pre className="text-sm text-primary-900 dark:text-gray-300 whitespace-pre-wrap font-mono bg-gray-50 dark:bg-gray-800 p-3 rounded-lg overflow-x-auto max-h-96">
            {chunk.content}
          </pre>
          {Object.keys(chunk.metadata).length > 0 && (
            <div className="mt-3 pt-3 border-t border-surface-variant dark:border-gray-700">
              <p className="text-xs font-medium text-secondary mb-2">Metadata:</p>
              <pre className="text-xs text-secondary bg-gray-50 dark:bg-gray-800 p-2 rounded overflow-x-auto">
                {JSON.stringify(chunk.metadata, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function DocumentPreviewPage() {
  const { documentId } = useParams<{ documentId: string }>()
  const navigate = useNavigate()
  const [doc, setDoc] = useState<DocumentFullInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showContent, setShowContent] = useState(false)
  const [openingExplorer, setOpeningExplorer] = useState(false)
  const [explorerMessage, setExplorerMessage] = useState<string | null>(null)
  
  // Cloud source state
  const [cloudSourceInfo, setCloudSourceInfo] = useState<{
    is_cloud_source: boolean
    provider?: string
    connection_id?: string
    web_view_url?: string
    remote_path?: string
    is_cached?: boolean
  } | null>(null)
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [_loadingCloudInfo, setLoadingCloudInfo] = useState(false)

  useEffect(() => {
    if (!documentId) return

    const fetchDocument = async () => {
      setLoading(true)
      setError(null)
      try {
        const data = await documentsApi.getFullInfo(documentId)
        setDoc(data)
        
        // Check if this is a cloud source document
        setLoadingCloudInfo(true)
        try {
          const cloudInfo = await cloudSourcesApi.getCloudSourceInfo(documentId)
          setCloudSourceInfo(cloudInfo)
        } catch (cloudErr) {
          // Not a cloud source or error fetching info
          setCloudSourceInfo({ is_cloud_source: false })
        } finally {
          setLoadingCloudInfo(false)
        }
      } catch (err) {
        console.error('Error fetching document:', err)
        setError('Failed to load document details')
      } finally {
        setLoading(false)
      }
    }

    fetchDocument()
  }, [documentId])

  const handleOpenExplorer = async () => {
    if (!documentId) return
    
    // For cloud sources, open in cloud provider
    if (cloudSourceInfo?.is_cloud_source && cloudSourceInfo.web_view_url) {
      window.open(cloudSourceInfo.web_view_url, '_blank')
      return
    }
    
    // For local files, open in OS explorer
    setOpeningExplorer(true)
    setExplorerMessage(null)
    try {
      const result = await documentsApi.openInExplorer(documentId)
      setExplorerMessage(result.message)
      if (!result.success && result.file_path) {
        setExplorerMessage(`${result.message}\nPath: ${result.file_path}`)
      }
    } catch (err) {
      setExplorerMessage('Failed to open file explorer')
    } finally {
      setOpeningExplorer(false)
    }
  }

  const handleOpenPreview = async () => {
    if (!documentId) return
    
    // For cloud sources, use cached file URL
    if (cloudSourceInfo?.is_cloud_source && cloudSourceInfo.connection_id) {
      // Trigger cache download and open
      try {
        await cloudSourcesApi.getCachedFile(documentId, cloudSourceInfo.connection_id)
        const url = cloudSourcesApi.getCachedFileUrl(cloudSourceInfo.connection_id, documentId)
        window.open(url, '_blank')
      } catch (err) {
        console.error('Failed to cache file:', err)
        // Fallback to web view if available
        if (cloudSourceInfo.web_view_url) {
          window.open(cloudSourceInfo.web_view_url, '_blank')
        }
      }
      return
    }
    
    // For local files, use direct file URL
    window.open(documentsApi.getFileUrl(documentId), '_blank')
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
      </div>
    )
  }

  if (error || !doc) {
    return (
      <div className="text-center py-12">
        <DocumentTextIcon className="h-12 w-12 text-secondary mx-auto mb-4" />
        <p className="text-secondary">{error || 'Document not found'}</p>
        <button
          onClick={() => navigate(-1)}
          className="mt-4 text-primary hover:underline"
        >
          Go back
        </button>
      </div>
    )
  }

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <button
          onClick={() => navigate(-1)}
          className="p-2 rounded-full hover:bg-surface-variant dark:hover:bg-gray-700 transition-colors"
        >
          <ArrowLeftIcon className="h-5 w-5 text-secondary" />
        </button>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold text-primary-900 dark:text-primary-200">
            {doc.title}
          </h1>
          <p className="text-sm text-secondary">{doc.source}</p>
        </div>
      </div>

      {/* Action buttons */}
      <div className="flex flex-wrap gap-3">
        {/* Cloud Source Badge */}
        {cloudSourceInfo?.is_cloud_source && (
          <div className="flex items-center gap-2 px-3 py-2 bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400 rounded-xl text-sm">
            <CloudIcon className="h-4 w-4" />
            <span>Cloud Source: {cloudSourceInfo.provider?.replace('_', ' ')}</span>
          </div>
        )}
        
        <button
          onClick={handleOpenExplorer}
          disabled={openingExplorer || (cloudSourceInfo?.is_cloud_source && !cloudSourceInfo.web_view_url)}
          className="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-xl hover:bg-primary-700 transition-colors disabled:opacity-50"
        >
          {cloudSourceInfo?.is_cloud_source ? (
            <>
              <ArrowTopRightOnSquareIcon className="h-5 w-5" />
              {openingExplorer ? 'Opening...' : 'Open in Cloud Provider'}
            </>
          ) : (
            <>
              <FolderOpenIcon className="h-5 w-5" />
              {openingExplorer ? 'Opening...' : 'Open in Explorer'}
            </>
          )}
        </button>
        <button
          onClick={handleOpenPreview}
          className="flex items-center gap-2 px-4 py-2 bg-surface-variant dark:bg-gray-700 text-primary-900 dark:text-primary-200 rounded-xl hover:bg-primary-100 dark:hover:bg-gray-600 transition-colors"
        >
          <EyeIcon className="h-5 w-5" />
          {cloudSourceInfo?.is_cloud_source ? 'Open Here (Cached)' : 'Open Preview'}
        </button>
        <Link
          to="/documents"
          className="flex items-center gap-2 px-4 py-2 bg-surface-variant dark:bg-gray-700 text-primary-900 dark:text-primary-200 rounded-xl hover:bg-primary-100 dark:hover:bg-gray-600 transition-colors"
        >
          <DocumentIcon className="h-5 w-5" />
          All Documents
        </Link>
      </div>

      {explorerMessage && (
        <div className={`p-3 rounded-xl text-sm ${explorerMessage.includes('Failed') || explorerMessage.includes('not') ? 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400' : 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'}`}>
          <pre className="whitespace-pre-wrap">{explorerMessage}</pre>
        </div>
      )}

      {/* Document Info Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* File Info */}
        <div className="bg-white dark:bg-gray-800 rounded-2xl p-4 shadow-sm">
          <div className="flex items-center gap-2 mb-3">
            <DocumentIcon className="h-5 w-5 text-primary" />
            <h3 className="font-medium text-primary-900 dark:text-primary-200">File Info</h3>
          </div>
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-secondary">Status:</dt>
              <dd className={doc.file_exists ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}>
                {doc.file_exists ? 'Available' : 'Not Found'}
              </dd>
            </div>
            {doc.file_stats && (
              <>
                <div className="flex justify-between">
                  <dt className="text-secondary">Size:</dt>
                  <dd className="text-primary-900 dark:text-gray-300">{formatBytes(doc.file_stats.size_bytes)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-secondary">Type:</dt>
                  <dd className="text-primary-900 dark:text-gray-300">{doc.file_stats.extension || 'Unknown'}</dd>
                </div>
              </>
            )}
            <div className="flex justify-between">
              <dt className="text-secondary">Content Length:</dt>
              <dd className="text-primary-900 dark:text-gray-300">{doc.content_length.toLocaleString()} chars</dd>
            </div>
          </dl>
        </div>

        {/* Chunks Info */}
        <div className="bg-white dark:bg-gray-800 rounded-2xl p-4 shadow-sm">
          <div className="flex items-center gap-2 mb-3">
            <CubeIcon className="h-5 w-5 text-primary" />
            <h3 className="font-medium text-primary-900 dark:text-primary-200">Chunks (Semantic)</h3>
          </div>
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-secondary">Total Chunks:</dt>
              <dd className="text-primary-900 dark:text-gray-300">{doc.chunks_count}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-secondary">Total Tokens:</dt>
              <dd className="text-primary-900 dark:text-gray-300">{doc.total_tokens.toLocaleString()}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-secondary">Avg Tokens/Chunk:</dt>
              <dd className="text-primary-900 dark:text-gray-300">
                {doc.chunks_count > 0 ? Math.round(doc.total_tokens / doc.chunks_count) : 0}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-secondary">Embeddings:</dt>
              <dd className="text-green-600 dark:text-green-400">
                {doc.chunks.filter(c => c.has_embedding).length}/{doc.chunks_count}
              </dd>
            </div>
          </dl>
        </div>

        {/* Metadata */}
        <div className="bg-white dark:bg-gray-800 rounded-2xl p-4 shadow-sm">
          <div className="flex items-center gap-2 mb-3">
            <ClockIcon className="h-5 w-5 text-primary" />
            <h3 className="font-medium text-primary-900 dark:text-primary-200">Timestamps</h3>
          </div>
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-secondary">Ingested:</dt>
              <dd className="text-primary-900 dark:text-gray-300">{formatDate(doc.created_at)}</dd>
            </div>
            {doc.metadata.ingestion_date !== undefined && doc.metadata.ingestion_date !== null && (
              <div className="flex justify-between">
                <dt className="text-secondary">Ingestion Date:</dt>
                <dd className="text-primary-900 dark:text-gray-300 text-xs">
                  {String(doc.metadata.ingestion_date).split('T')[0]}
                </dd>
              </div>
            )}
          </dl>
        </div>
      </div>

      {/* File Path / Cloud Source Path */}
      {(doc.file_path || cloudSourceInfo?.remote_path) && (
        <div className="bg-white dark:bg-gray-800 rounded-2xl p-4 shadow-sm">
          <h3 className="font-medium text-primary-900 dark:text-primary-200 mb-2">
            {cloudSourceInfo?.is_cloud_source ? 'Cloud Path' : 'File Path'}
          </h3>
          <code className="block text-sm bg-gray-50 dark:bg-gray-900 p-3 rounded-lg text-secondary overflow-x-auto">
            {cloudSourceInfo?.is_cloud_source ? cloudSourceInfo.remote_path : doc.file_path}
          </code>
          {cloudSourceInfo?.is_cloud_source && cloudSourceInfo.is_cached && (
            <p className="text-xs text-green-600 dark:text-green-400 mt-2">
              ✓ Cached locally for preview
            </p>
          )}
        </div>
      )}

      {/* Document Metadata */}
      {Object.keys(doc.metadata).length > 0 && (
        <div className="bg-white dark:bg-gray-800 rounded-2xl p-4 shadow-sm">
          <h3 className="font-medium text-primary-900 dark:text-primary-200 mb-3">Document Metadata</h3>
          <pre className="text-sm bg-gray-50 dark:bg-gray-900 p-3 rounded-lg text-secondary overflow-x-auto">
            {JSON.stringify(doc.metadata, null, 2)}
          </pre>
        </div>
      )}

      {/* Raw Content Preview */}
      <div className="bg-white dark:bg-gray-800 rounded-2xl p-4 shadow-sm">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-medium text-primary-900 dark:text-primary-200">Document Content (Text)</h3>
          <button
            onClick={() => setShowContent(!showContent)}
            className="text-sm text-primary hover:underline"
          >
            {showContent ? 'Hide' : 'Show'} Content
          </button>
        </div>
        {showContent && (
          <pre className="text-sm bg-gray-50 dark:bg-gray-900 p-4 rounded-lg text-primary-900 dark:text-gray-300 whitespace-pre-wrap overflow-x-auto max-h-96">
            {doc.content || 'No content available'}
          </pre>
        )}
      </div>

      {/* Chunks Section */}
      <div className="bg-white dark:bg-gray-800 rounded-2xl p-4 shadow-sm">
        <h3 className="font-medium text-primary-900 dark:text-primary-200 mb-4">
          Chunks ({doc.chunks_count})
        </h3>
        <div className="space-y-3">
          {doc.chunks.map((chunk, index) => (
            <ChunkCard key={chunk.id} chunk={chunk} index={index} />
          ))}
        </div>
      </div>
    </div>
  )
}

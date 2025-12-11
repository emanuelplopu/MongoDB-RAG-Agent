import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  DocumentTextIcon,
  TrashIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ArrowPathIcon,
} from '@heroicons/react/24/outline'
import { documentsApi, Document, DocumentListResponse } from '../api/client'

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<Document[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [totalPages, setTotalPages] = useState(1)
  const [total, setTotal] = useState(0)

  const fetchDocuments = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const response: DocumentListResponse = await documentsApi.list(page, pageSize)
      setDocuments(response.documents)
      setTotalPages(response.total_pages)
      setTotal(response.total)
    } catch (err) {
      console.error('Error fetching documents:', err)
      setError('Failed to load documents.')
    } finally {
      setIsLoading(false)
    }
  }

  useEffect(() => {
    fetchDocuments()
  }, [page])

  const handleDelete = async (documentId: string) => {
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
    return new Date(dateStr).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-200">Documents</h2>
          <p className="text-sm text-secondary dark:text-gray-400">
            {total} documents in your knowledge base
          </p>
        </div>
        <button
          onClick={fetchDocuments}
          disabled={isLoading}
          className="flex items-center gap-2 rounded-xl bg-surface-variant dark:bg-gray-700 px-4 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 transition-all hover:bg-primary-100 dark:hover:bg-gray-600"
        >
          <ArrowPathIcon className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-2xl bg-red-50 dark:bg-red-900/30 p-4 text-red-700 dark:text-red-400">{error}</div>
      )}

      {/* Documents list */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
        </div>
      ) : documents.length === 0 ? (
        <div className="rounded-2xl bg-surface-variant dark:bg-gray-800 p-8 text-center">
          <DocumentTextIcon className="mx-auto h-12 w-12 text-secondary dark:text-gray-500 mb-3" />
          <p className="text-secondary dark:text-gray-400">No documents found.</p>
          <p className="text-sm text-secondary dark:text-gray-500 mt-1">
            Use the ingestion feature to add documents.
          </p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-2xl bg-surface dark:bg-gray-800 shadow-elevation-1">
          <table className="min-w-full divide-y divide-surface-variant dark:divide-gray-700">
            <thead className="bg-surface-variant dark:bg-gray-700">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-secondary dark:text-gray-400">
                  Title
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-secondary dark:text-gray-400">
                  Source
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-secondary dark:text-gray-400">
                  Chunks
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium uppercase tracking-wider text-secondary dark:text-gray-400">
                  Created
                </th>
                <th className="px-6 py-3 text-right text-xs font-medium uppercase tracking-wider text-secondary dark:text-gray-400">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-surface-variant dark:divide-gray-700 bg-white dark:bg-gray-900">
              {documents.map((doc) => (
                <tr key={doc.id} className="hover:bg-surface-variant/50 dark:hover:bg-gray-800 transition-colors">
                  <td className="whitespace-nowrap px-6 py-4">
                    <Link 
                      to={`/documents/${doc.id}`}
                      className="flex items-center gap-3 group"
                    >
                      <DocumentTextIcon className="h-5 w-5 text-primary" />
                      <span className="font-medium text-primary-900 dark:text-primary-200 group-hover:text-primary group-hover:underline">
                        {doc.title}
                      </span>
                    </Link>
                  </td>
                  <td className="px-6 py-4 text-sm text-secondary dark:text-gray-400 max-w-xs truncate">
                    {doc.source}
                  </td>
                  <td className="whitespace-nowrap px-6 py-4">
                    <span className="rounded-lg bg-primary-100 dark:bg-primary-900/50 px-2 py-1 text-xs font-medium text-primary-700 dark:text-primary-300">
                      {doc.chunks_count}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-6 py-4 text-sm text-secondary dark:text-gray-400">
                    {formatDate(doc.created_at)}
                  </td>
                  <td className="whitespace-nowrap px-6 py-4 text-right">
                    <button
                      onClick={() => handleDelete(doc.id)}
                      className="rounded-lg p-2 text-red-500 hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors"
                      title="Delete document"
                    >
                      <TrashIcon className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <p className="text-sm text-secondary dark:text-gray-400">
            Page {page} of {totalPages}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="flex items-center gap-1 rounded-lg bg-surface-variant dark:bg-gray-700 px-3 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 disabled:opacity-50 disabled:cursor-not-allowed hover:bg-primary-100 dark:hover:bg-gray-600 transition-colors"
            >
              <ChevronLeftIcon className="h-4 w-4" />
              Previous
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="flex items-center gap-1 rounded-lg bg-surface-variant dark:bg-gray-700 px-3 py-2 text-sm font-medium text-primary-700 dark:text-primary-300 disabled:opacity-50 disabled:cursor-not-allowed hover:bg-primary-100 dark:hover:bg-gray-600 transition-colors"
            >
              Next
              <ChevronRightIcon className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

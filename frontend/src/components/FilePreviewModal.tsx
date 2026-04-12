import { useState, useEffect, useCallback } from 'react'
import { XMarkIcon, ArrowDownTrayIcon, ArrowTopRightOnSquareIcon } from '@heroicons/react/24/outline'
import ReactMarkdown from 'react-markdown'

// File type categories
const IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'ico']
const PDF_EXTENSIONS = ['pdf']
const AUDIO_EXTENSIONS = ['mp3', 'wav', 'flac', 'm4a', 'ogg', 'aac', 'wma']
const VIDEO_EXTENSIONS = ['mp4', 'avi', 'mkv', 'mov', 'webm', 'ogv']
const DOCX_EXTENSIONS = ['docx']
const XLSX_EXTENSIONS = ['xlsx', 'xls']
const TEXT_EXTENSIONS = ['txt', 'log', 'csv', 'tsv', 'ini', 'cfg', 'conf', 'env']
const CODE_EXTENSIONS = ['json', 'xml', 'html', 'htm', 'css', 'js', 'ts', 'jsx', 'tsx', 'py', 'java', 'c', 'cpp', 'h', 'hpp', 'rs', 'go', 'rb', 'php', 'sh', 'bash', 'yaml', 'yml', 'toml', 'sql']
const MARKDOWN_EXTENSIONS = ['md', 'markdown']

function getExtension(filename: string): string {
  return (filename.split('.').pop() || '').toLowerCase()
}

function getFileCategory(filename: string): 'image' | 'pdf' | 'audio' | 'video' | 'docx' | 'xlsx' | 'text' | 'code' | 'markdown' | 'unknown' {
  const ext = getExtension(filename)
  if (IMAGE_EXTENSIONS.includes(ext)) return 'image'
  if (PDF_EXTENSIONS.includes(ext)) return 'pdf'
  if (AUDIO_EXTENSIONS.includes(ext)) return 'audio'
  if (VIDEO_EXTENSIONS.includes(ext)) return 'video'
  if (DOCX_EXTENSIONS.includes(ext)) return 'docx'
  if (XLSX_EXTENSIONS.includes(ext)) return 'xlsx'
  if (MARKDOWN_EXTENSIONS.includes(ext)) return 'markdown'
  if (CODE_EXTENSIONS.includes(ext)) return 'code'
  if (TEXT_EXTENSIONS.includes(ext)) return 'text'
  return 'unknown'
}

interface FilePreviewModalProps {
  isOpen: boolean
  onClose: () => void
  fileUrl: string
  filename: string
  /** Fallback text content (from document.content) for unsupported binary types */
  fallbackContent?: string
}

export default function FilePreviewModal({ isOpen, onClose, fileUrl, filename, fallbackContent }: FilePreviewModalProps) {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [renderedContent, setRenderedContent] = useState<string>('')
  const [xlsxHtml, setXlsxHtml] = useState<string>('')

  const category = getFileCategory(filename)

  // Close on Escape
  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Escape') onClose()
  }, [onClose])

  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleKeyDown)
      document.body.style.overflow = 'hidden'
      return () => {
        document.removeEventListener('keydown', handleKeyDown)
        document.body.style.overflow = ''
      }
    }
  }, [isOpen, handleKeyDown])

  // Fetch and process content for text-based and convertible types
  useEffect(() => {
    if (!isOpen) return
    setLoading(true)
    setError(null)
    setRenderedContent('')
    setXlsxHtml('')

    const inlineUrl = fileUrl + (fileUrl.includes('?') ? '&' : '?') + 'inline=true'

    if (category === 'docx') {
      // Fetch DOCX as ArrayBuffer and convert with mammoth
      fetch(inlineUrl)
        .then(res => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          return res.arrayBuffer()
        })
        .then(async (buffer) => {
          const mammoth = await import('mammoth')
          const result = await mammoth.convertToHtml({ arrayBuffer: buffer })
          setRenderedContent(result.value)
          setLoading(false)
        })
        .catch(err => {
          console.error('DOCX preview error:', err)
          setError('Failed to render DOCX preview')
          setLoading(false)
        })
    } else if (category === 'xlsx') {
      // Fetch XLSX as ArrayBuffer and render with SheetJS
      fetch(inlineUrl)
        .then(res => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          return res.arrayBuffer()
        })
        .then(async (buffer) => {
          const XLSX = await import('xlsx')
          const workbook = XLSX.read(buffer, { type: 'array' })
          // Convert first sheet to HTML table
          const sheetName = workbook.SheetNames[0]
          if (sheetName) {
            const sheet = workbook.Sheets[sheetName]
            const html = XLSX.utils.sheet_to_html(sheet, { editable: false })
            setXlsxHtml(html)
          }
          setLoading(false)
        })
        .catch(err => {
          console.error('XLSX preview error:', err)
          setError('Failed to render spreadsheet preview')
          setLoading(false)
        })
    } else if (category === 'text' || category === 'code' || category === 'markdown') {
      // Fetch as text
      fetch(inlineUrl)
        .then(res => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          return res.text()
        })
        .then(text => {
          setRenderedContent(text)
          setLoading(false)
        })
        .catch(err => {
          console.error('Text preview error:', err)
          setError('Failed to load text content')
          setLoading(false)
        })
    } else if (category === 'image' || category === 'pdf' || category === 'audio' || category === 'video') {
      // These use direct URL embedding — just mark as loaded
      setLoading(false)
    } else {
      // Unknown type — use fallback content
      if (fallbackContent) {
        setRenderedContent(fallbackContent)
      }
      setLoading(false)
    }
  }, [isOpen, fileUrl, category, fallbackContent])

  if (!isOpen) return null

  const inlineUrl = fileUrl + (fileUrl.includes('?') ? '&' : '?') + 'inline=true'

  const handleDownload = () => {
    window.open(fileUrl, '_blank')
  }

  const handleOpenNewTab = () => {
    window.open(inlineUrl, '_blank')
  }

  const renderPreviewContent = () => {
    if (loading) {
      return (
        <div className="flex items-center justify-center h-full min-h-[400px]">
          <div className="text-center">
            <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-primary mx-auto mb-3"></div>
            <p className="text-secondary text-sm">Loading preview...</p>
          </div>
        </div>
      )
    }

    if (error) {
      return (
        <div className="flex flex-col items-center justify-center h-full min-h-[400px] gap-4">
          <p className="text-red-500 text-sm">{error}</p>
          {fallbackContent && (
            <div className="w-full">
              <p className="text-xs text-secondary mb-2">Showing extracted text content instead:</p>
              <pre className="text-sm bg-gray-50 dark:bg-gray-900 p-4 rounded-lg text-primary-900 dark:text-gray-300 whitespace-pre-wrap overflow-auto max-h-[60vh]">
                {fallbackContent}
              </pre>
            </div>
          )}
        </div>
      )
    }

    switch (category) {
      case 'pdf':
        return (
          <iframe
            src={inlineUrl}
            className="w-full h-full min-h-[75vh] rounded-lg border-0"
            title={`Preview: ${filename}`}
          />
        )

      case 'image':
        return (
          <div className="flex items-center justify-center p-4 overflow-auto max-h-[80vh]">
            <img
              src={inlineUrl}
              alt={filename}
              className="max-w-full max-h-[75vh] object-contain rounded-lg shadow-lg"
              onError={() => setError('Failed to load image')}
            />
          </div>
        )

      case 'audio':
        return (
          <div className="flex flex-col items-center justify-center p-8 gap-4 min-h-[200px]">
            <div className="text-6xl mb-2">🎵</div>
            <p className="text-primary-900 dark:text-gray-200 font-medium">{filename}</p>
            <audio controls className="w-full max-w-lg" preload="metadata">
              <source src={inlineUrl} />
              Your browser does not support audio playback.
            </audio>
          </div>
        )

      case 'video':
        return (
          <div className="flex items-center justify-center p-4">
            <video
              controls
              className="max-w-full max-h-[75vh] rounded-lg shadow-lg"
              preload="metadata"
            >
              <source src={inlineUrl} />
              Your browser does not support video playback.
            </video>
          </div>
        )

      case 'docx':
        return (
          <div
            className="prose dark:prose-invert max-w-none p-6 overflow-auto max-h-[80vh] bg-white dark:bg-gray-900 rounded-lg"
            dangerouslySetInnerHTML={{ __html: renderedContent }}
          />
        )

      case 'xlsx':
        return (
          <div
            className="overflow-auto max-h-[80vh] p-4 [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-gray-300 [&_td]:dark:border-gray-600 [&_td]:px-2 [&_td]:py-1 [&_td]:text-sm [&_th]:border [&_th]:border-gray-300 [&_th]:dark:border-gray-600 [&_th]:px-2 [&_th]:py-1 [&_th]:text-sm [&_th]:bg-gray-100 [&_th]:dark:bg-gray-700 [&_table]:text-primary-900 [&_table]:dark:text-gray-300"
            dangerouslySetInnerHTML={{ __html: xlsxHtml }}
          />
        )

      case 'markdown':
        return (
          <div className="prose dark:prose-invert max-w-none p-6 overflow-auto max-h-[80vh]">
            <ReactMarkdown>{renderedContent}</ReactMarkdown>
          </div>
        )

      case 'code':
        return (
          <pre className="text-sm bg-gray-50 dark:bg-gray-900 p-6 rounded-lg text-primary-900 dark:text-gray-300 whitespace-pre-wrap overflow-auto max-h-[80vh] font-mono">
            {renderedContent}
          </pre>
        )

      case 'text':
        return (
          <pre className="text-sm bg-gray-50 dark:bg-gray-900 p-6 rounded-lg text-primary-900 dark:text-gray-300 whitespace-pre-wrap overflow-auto max-h-[80vh]">
            {renderedContent}
          </pre>
        )

      default:
        // Unknown binary — show fallback text content
        return (
          <div className="p-6">
            {fallbackContent ? (
              <>
                <p className="text-xs text-secondary mb-3">
                  No native preview available for .{getExtension(filename)} files. Showing extracted text content:
                </p>
                <pre className="text-sm bg-gray-50 dark:bg-gray-900 p-4 rounded-lg text-primary-900 dark:text-gray-300 whitespace-pre-wrap overflow-auto max-h-[70vh]">
                  {fallbackContent}
                </pre>
              </>
            ) : (
              <div className="flex flex-col items-center justify-center min-h-[300px] gap-4">
                <p className="text-secondary">
                  Preview is not available for .{getExtension(filename)} files.
                </p>
                <button
                  onClick={handleDownload}
                  className="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-xl hover:bg-primary-700 transition-colors"
                >
                  <ArrowDownTrayIcon className="h-5 w-5" />
                  Download File
                </button>
              </div>
            )}
          </div>
        )
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-5xl max-h-[95vh] flex flex-col overflow-hidden">
        {/* Modal Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200 dark:border-gray-700 shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <h2 className="text-lg font-semibold text-primary-900 dark:text-primary-200 truncate">
              {filename}
            </h2>
            <span className="text-xs px-2 py-0.5 bg-gray-100 dark:bg-gray-700 text-secondary rounded-full uppercase shrink-0">
              {getExtension(filename) || '?'}
            </span>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={handleOpenNewTab}
              className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-secondary hover:text-primary-900 dark:hover:text-gray-200"
              title="Open in new tab"
            >
              <ArrowTopRightOnSquareIcon className="h-5 w-5" />
            </button>
            <button
              onClick={handleDownload}
              className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-secondary hover:text-primary-900 dark:hover:text-gray-200"
              title="Download"
            >
              <ArrowDownTrayIcon className="h-5 w-5" />
            </button>
            <button
              onClick={onClose}
              className="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-secondary hover:text-primary-900 dark:hover:text-gray-200"
              title="Close (Esc)"
            >
              <XMarkIcon className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Modal Content */}
        <div className="flex-1 overflow-auto">
          {renderPreviewContent()}
        </div>
      </div>
    </div>
  )
}

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
const translations: Record<string, string> = {
  'docPreviewPage.failedToLoad': 'Failed to load document details',
  'docPreviewPage.notFound': 'Document not found',
  'docPreviewPage.goBack': 'Go back',
  'docPreviewPage.openInExplorer': 'Open in Explorer',
  'docPreviewPage.openInCloud': 'Open in Cloud Provider',
  'docPreviewPage.openPreview': 'Open Preview',
  'docPreviewPage.openCached': 'Open Here (Cached)',
  'docPreviewPage.opening': 'Opening...',
  'docPreviewPage.allDocuments': 'All Documents',
  'docPreviewPage.fileInfo': 'File Info',
  'docPreviewPage.statusLabel': 'Status',
  'docPreviewPage.available': 'Available',
  'docPreviewPage.notFoundStatus': 'Not Found',
  'docPreviewPage.size': 'Size',
  'docPreviewPage.type': 'Type',
  'docPreviewPage.contentLength': 'Content Length',
  'docPreviewPage.chunksSemantic': 'Chunks & Semantics',
  'docPreviewPage.totalChunks': 'Total Chunks',
  'docPreviewPage.totalTokens': 'Total Tokens',
  'docPreviewPage.avgTokensChunk': 'Avg Tokens/Chunk',
  'docPreviewPage.embeddings': 'Embeddings',
  'docPreviewPage.timestamps': 'Timestamps',
  'docPreviewPage.ingested': 'Ingested',
  'docPreviewPage.ingestionDate': 'Ingestion Date',
  'docPreviewPage.filePath': 'File Path',
  'docPreviewPage.cloudPath': 'Cloud Path',
  'docPreviewPage.cachedLocally': 'Cached locally for preview',
  'docPreviewPage.documentMetadata': 'Document Metadata',
  'docPreviewPage.documentContent': 'Document Content',
  'docPreviewPage.show': 'Show',
  'docPreviewPage.hide': 'Hide',
  'docPreviewPage.noContent': 'No content',
  'docPreviewPage.copyPath': 'Copy Path',
  'docPreviewPage.failedExplorer': 'Failed to open explorer',
  'docPreviewPage.metadata': 'Metadata:',
}
const stableT = (key: string, params?: Record<string, unknown>) => {
  if (key === 'docPreviewPage.embedded') return `Embedded (${params?.dims ?? 0}d)`
  if (key === 'docPreviewPage.tokensCount') return `${params?.count ?? 0} tokens`
  if (key === 'docPreviewPage.chunkNumber') return `Chunk ${params?.number ?? 0}`
  if (key === 'docPreviewPage.chunksCount') return `${params?.count ?? 0} Chunks`
  if (key === 'docPreviewPage.cloudSource') return `Cloud Source: ${params?.provider ?? ''}`
  return translations[key] ?? key
}
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: stableT, i18n: { language: 'en', changeLanguage: async () => {} } }),
  Trans: ({ children }: { children?: unknown }) => children,
  initReactI18next: { type: '3rdParty', init: () => {} },
}))
import type { DocumentFullInfo } from '../api/client'
import DocumentPreviewPage from './DocumentPreviewPage'

const navigateMock = vi.fn()
const getFullInfoMock = vi.fn()
const openInExplorerMock = vi.fn()
const getFileUrlMock = vi.fn()
const getCloudSourceInfoMock = vi.fn()
const getCachedFileMock = vi.fn()
const getCachedFileUrlMock = vi.fn()
const windowOpenMock = vi.fn()

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return {
    ...actual,
    useNavigate: () => navigateMock,
  }
})

vi.mock('../api/client', () => ({
  documentsApi: {
    getFullInfo: (...args: unknown[]) => getFullInfoMock(...args),
    openInExplorer: (...args: unknown[]) => openInExplorerMock(...args),
    getFileUrl: (...args: unknown[]) => getFileUrlMock(...args),
  },
  cloudSourcesApi: {
    getCloudSourceInfo: (...args: unknown[]) => getCloudSourceInfoMock(...args),
    getCachedFile: (...args: unknown[]) => getCachedFileMock(...args),
    getCachedFileUrl: (...args: unknown[]) => getCachedFileUrlMock(...args),
  },
}))

vi.mock('../components/FilePreviewModal', () => ({
  default: ({
    isOpen,
    fileUrl,
    filename,
    fallbackContent,
  }: {
    isOpen: boolean
    fileUrl: string
    filename: string
    fallbackContent?: string
  }) => (
    isOpen ? (
      <div data-testid="preview-modal">
        <span>{filename}</span>
        <span>{fileUrl}</span>
        <span>{fallbackContent}</span>
      </div>
    ) : null
  ),
}))

const chunkFixture = {
  id: 'chunk-1',
  content: 'Chunk body text',
  chunk_index: 0,
  token_count: 42,
  metadata: { heading: 'Intro' },
  has_embedding: true,
  embedding_dimensions: 1536,
}

const documentFixture: DocumentFullInfo = {
  id: 'doc-1',
  title: 'Quarterly Report',
  source: 'reports/q1-report.pdf',
  content: 'Full extracted content',
  content_length: 1200,
  created_at: '2026-04-18T08:30:00Z',
  metadata: {
    ingestion_date: '2026-04-17T12:00:00Z',
    author: 'Finance Team',
  },
  file_path: 'C:/docs/reports/q1-report.pdf',
  file_exists: true,
  file_stats: {
    size_bytes: 1536,
    modified_at: '2026-04-17T11:00:00Z',
    extension: 'pdf',
  },
  chunks_count: 1,
  total_tokens: 42,
  chunks: [chunkFixture],
}

function renderPage(initialPath = '/documents/doc-1') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/documents/:documentId" element={<DocumentPreviewPage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('DocumentPreviewPage', () => {
  beforeEach(() => {
    navigateMock.mockReset()
    getFullInfoMock.mockReset()
    openInExplorerMock.mockReset()
    getFileUrlMock.mockReset()
    getCloudSourceInfoMock.mockReset()
    getCachedFileMock.mockReset()
    getCachedFileUrlMock.mockReset()
    windowOpenMock.mockReset()

    Object.defineProperty(window, 'open', {
      configurable: true,
      writable: true,
      value: windowOpenMock,
    })
    Object.defineProperty(window.navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: vi.fn(),
      },
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('renders local document details, expands chunks, opens previews, and navigates back', async () => {
    getFullInfoMock.mockResolvedValue(documentFixture)
    getCloudSourceInfoMock.mockRejectedValue(new Error('not a cloud source'))
    getFileUrlMock.mockReturnValue('/api/v1/ingestion/documents/doc-1/file')
    openInExplorerMock.mockResolvedValue({
      success: true,
      message: 'Opened in file explorer',
      is_docker: false,
    })
    const user = userEvent.setup()

    renderPage()

    expect(await screen.findByRole('heading', { name: 'Quarterly Report' })).toBeInTheDocument()
    expect(screen.getByText('reports/q1-report.pdf')).toBeInTheDocument()
    expect(screen.getByText('1.5 KB')).toBeInTheDocument()
    expect(screen.getByText('pdf')).toBeInTheDocument()
    expect(screen.getByText(/1[,.]200 chars/)).toBeInTheDocument()
    expect(screen.getAllByText('42').length).toBeGreaterThan(0)
    expect(screen.getByText('Embedded (1536d)')).toBeInTheDocument()
    expect(screen.getByText('C:/docs/reports/q1-report.pdf')).toBeInTheDocument()
    expect(screen.getByText(/Finance Team/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Show Content' }))
    expect(screen.getByText('Full extracted content')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Chunk 1/ }))
    expect(screen.getByText('Chunk body text')).toBeInTheDocument()
    expect(screen.getByText('Metadata:')).toBeInTheDocument()
    expect(screen.getByText(/Intro/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Open in Explorer' }))
    expect(await screen.findByText('Opened in file explorer')).toBeInTheDocument()
    expect(openInExplorerMock).toHaveBeenCalledWith('doc-1')

    await user.click(screen.getByRole('button', { name: 'Open Preview' }))
    expect(screen.getByTestId('preview-modal')).toHaveTextContent('reports/q1-report.pdf')
    expect(screen.getByTestId('preview-modal')).toHaveTextContent('/api/v1/ingestion/documents/doc-1/file')
    expect(screen.getByTestId('preview-modal')).toHaveTextContent('Full extracted content')

    await user.click(screen.getAllByRole('button')[0])
    expect(navigateMock).toHaveBeenCalledWith(-1)
  })

  it('shows a docker host path message and copies the translated path', async () => {
    const user = userEvent.setup()
    const clipboardWriteTextMock = vi.fn()
    Object.defineProperty(window.navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: clipboardWriteTextMock,
      },
    })
    getFullInfoMock.mockResolvedValue(documentFixture)
    getCloudSourceInfoMock.mockResolvedValue({ is_cloud_source: false })
    openInExplorerMock.mockResolvedValue({
      success: true,
      is_docker: true,
      host_path: 'D:/mapped/reports/q1-report.pdf',
    })
    renderPage()

    expect(await screen.findByText('Quarterly Report')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Open in Explorer' }))
    expect(
      await screen.findByText((content) => content.includes('D:/mapped/reports/q1-report.pdf'))
    ).toBeInTheDocument()

    const copyButton = screen.getByRole('button', { name: 'Copy Path' })
    copyButton.focus()
    await user.click(copyButton)

    expect(clipboardWriteTextMock).toHaveBeenCalledWith('D:/mapped/reports/q1-report.pdf')
    expect(copyButton.innerText).toBe('Copied!')
  })

  it('renders cloud-source actions and falls back to the provider web view when caching fails', async () => {
    getFullInfoMock.mockResolvedValue(documentFixture)
    getCloudSourceInfoMock.mockResolvedValue({
      is_cloud_source: true,
      provider: 'google_drive',
      connection_id: 'conn-1',
      web_view_url: 'https://drive.example.com/file/123',
      remote_path: '/Drive/Reports/q1-report.pdf',
      is_cached: true,
    })
    getCachedFileMock
      .mockResolvedValueOnce({ local_path: '/cache/file.pdf' })
      .mockRejectedValueOnce(new Error('cache miss'))
    getCachedFileUrlMock.mockReturnValue('/cache/serve/conn-1/doc-1')
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const user = userEvent.setup()

    renderPage()

    expect(await screen.findByText('Cloud Source: google drive')).toBeInTheDocument()
    expect(screen.getByText('/Drive/Reports/q1-report.pdf')).toBeInTheDocument()
    expect(screen.getByText(/Cached locally for preview/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Open in Cloud Provider' }))
    expect(windowOpenMock).toHaveBeenCalledWith('https://drive.example.com/file/123', '_blank')

    await user.click(screen.getByRole('button', { name: 'Open Here (Cached)' }))
    await waitFor(() => {
      expect(getCachedFileMock).toHaveBeenCalledWith('doc-1', 'conn-1')
    })
    expect(screen.getByTestId('preview-modal')).toHaveTextContent('/cache/serve/conn-1/doc-1')

    await user.click(screen.getByRole('button', { name: 'Open Here (Cached)' }))
    await waitFor(() => {
      expect(windowOpenMock).toHaveBeenLastCalledWith('https://drive.example.com/file/123', '_blank')
    })

    consoleErrorSpy.mockRestore()
  })

  it('shows an error state when the document cannot be loaded', async () => {
    getFullInfoMock.mockRejectedValue(new Error('boom'))
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const user = userEvent.setup()

    renderPage()

    expect(await screen.findByText('Failed to load document details')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Go back' }))
    expect(navigateMock).toHaveBeenCalledWith(-1)

    consoleErrorSpy.mockRestore()
  })
})

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
const translations: Record<string, string> = {
  'documentsPage.folders': 'Folders',
  'documentsPage.allDocuments': 'All documents',
  'documentsPage.showFolders': 'Show folders',
  'documentsPage.documents': 'Documents',
  'documentsPage.searchPlaceholder': 'Search documents...',
  'documentsPage.sortDateModified': 'Date modified',
  'documentsPage.sortName': 'Name',
  'documentsPage.sortSize': 'Size',
  'documentsPage.sortType': 'Type',
  'documentsPage.sortDescending': 'Sort descending',
  'documentsPage.sortAscending': 'Sort ascending',
  'documentsPage.rebuildingMetadata': 'Rebuilding metadata...',
  'documentsPage.noSearchResults': 'No documents match your search',
  'documentsPage.tryDifferentKeywords': 'Try different keywords',
  'documentsPage.noDocumentsInFolder': 'No documents in this folder',
  'documentsPage.useIngestion': 'Use ingestion to add documents',
  'documentsPage.columnName': 'Name',
  'documentsPage.columnPath': 'Path',
  'documentsPage.columnSize': 'Size',
  'documentsPage.columnModified': 'Modified',
  'documentsPage.prev': 'Prev',
  'documentsPage.next': 'Next',
  'documentsPage.deleteConfirm': 'Are you sure you want to delete this document and all its chunks?',
  'documentsPage.notAvailable': 'N/A',
}
const stableT = (key: string, params?: Record<string, unknown>) => {
  if (key === 'documentsPage.chunksCount') return `${params?.count ?? 0} chunks`
  if (key === 'documentsPage.fixDocs') return `Fix ${params?.count ?? 0} docs`
  if (key === 'documentsPage.rebuildingPercent') return `Rebuilding... ${params?.percent ?? 0}%`
  if (key === 'documentsPage.rebuildComplete') return `Rebuild complete: ${params?.count ?? 0} docs updated`
  if (key === 'documentsPage.docsNeedRepair') return `${params?.count ?? 0} docs need repair`
  if (key === 'documentsPage.pageOfTotal') return `Page ${params?.page ?? 1} of ${params?.total ?? 1}`
  return translations[key] ?? key
}
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: stableT, i18n: { language: 'en', changeLanguage: async () => {} } }),
  Trans: ({ children }: { children?: unknown }) => children,
  initReactI18next: { type: '3rdParty', init: () => {} },
}))
import type {
  Document,
  DocumentListResponse,
  FolderInfo,
  FoldersResponse,
  MetadataRebuildStatus,
} from '../api/client'
import DocumentsPage from './DocumentsPage'

const listMock = vi.fn()
const getFoldersMock = vi.fn()
const deleteMock = vi.fn()
const getMetadataRebuildStatusMock = vi.fn()
const startMetadataRebuildMock = vi.fn()

vi.mock('react-arborist', () => ({
  Tree: ({
    data,
    onSelect,
    children,
  }: {
    data: Array<{ id: string; name: string; count?: number }>
    onSelect?: (nodes: Array<{ id: string }>) => void
    children?: (props: {
      node: {
        data: { id: string; name: string; isFolder: boolean; documentCount?: number }
        isSelected: boolean
        isOpen: boolean
        select: () => void
        toggle: () => void
      }
      style: React.CSSProperties
      dragHandle: null
    }) => React.ReactNode
  }) => (
    <div data-testid="folder-tree">
      {data.map((node, index) => (
        <div key={node.id}>
          {children?.({
            node: {
              data: {
                id: node.id,
                name: node.name,
                isFolder: true,
                documentCount: node.count,
              },
              isSelected: index === 0,
              isOpen: index === 0,
              select: () => onSelect?.([{ id: node.id }]),
              toggle: () => undefined,
            },
            style: {},
            dragHandle: null,
          })}
          <button onClick={() => onSelect?.([{ id: node.id }])}>
            {node.name}
          </button>
        </div>
      ))}
    </div>
  ),
  NodeRendererProps: {},
}))

vi.mock('../api/client', () => ({
  documentsApi: {
    list: (...args: unknown[]) => listMock(...args),
    getFolders: (...args: unknown[]) => getFoldersMock(...args),
    delete: (...args: unknown[]) => deleteMock(...args),
  },
  ingestionApi: {
    getMetadataRebuildStatus: (...args: unknown[]) => getMetadataRebuildStatusMock(...args),
    startMetadataRebuild: (...args: unknown[]) => startMetadataRebuildMock(...args),
  },
}))

const foldersFixture: FoldersResponse = {
  folders: [
    { path: 'media', name: 'media', depth: 0, count: 1 },
    { path: 'reports', name: 'reports', depth: 0, count: 2 },
    { path: 'reports/2026', name: '2026', depth: 1, count: 1 },
  ],
  total_folders: 3,
  total_documents: 3,
}

const rootDocuments: Document[] = [
  {
    id: 'doc-1',
    title: 'Quarterly Report',
    source: 'reports/q1-report.pdf',
    chunks_count: 0,
    created_at: '2026-04-18T09:30:00Z',
    metadata: {},
  },
  {
    id: 'doc-2',
    title: 'Team Photo',
    source: 'media/team-photo.jpg',
    chunks_count: 3,
    created_at: '2026-04-17T09:30:00Z',
    metadata: {},
  },
]

const reportsDocuments: Document[] = [
  {
    id: 'doc-3',
    title: 'Reports Index',
    source: 'reports/index.md',
    chunks_count: 2,
    created_at: '2026-04-16T09:30:00Z',
    metadata: {},
  },
]

const pageTwoDocuments: Document[] = [
  {
    id: 'doc-4',
    title: 'Page Two Report',
    source: 'reports/page-two.pdf',
    chunks_count: 4,
    created_at: '2026-04-15T09:30:00Z',
    metadata: {},
  },
]

const variedDocuments: Document[] = [
  {
    id: 'doc-audio',
    title: 'Audio Clip',
    source: 'media/audio.mp3',
    chunks_count: 0,
    created_at: 'not-a-date',
    metadata: {},
  },
  {
    id: 'doc-video',
    title: 'Video Tour',
    source: 'media/tour.mp4',
    chunks_count: 1,
    metadata: {},
  },
  {
    id: 'doc-word',
    title: 'Word Plan',
    source: 'plans/plan.docx',
    chunks_count: 2,
    created_at: '2026-04-14T09:30:00Z',
    metadata: {},
  },
  {
    id: 'doc-sheet',
    title: 'Budget Sheet',
    source: 'sheets/budget.xlsx',
    chunks_count: 3,
    created_at: '2026-04-13T09:30:00Z',
    metadata: {},
  },
  {
    id: 'doc-slides',
    title: 'Launch Deck',
    source: 'decks/launch.pptx',
    chunks_count: 4,
    created_at: '2026-04-12T09:30:00Z',
    metadata: {},
  },
  {
    id: 'doc-text',
    title: 'Plain Notes',
    source: 'notes/plain.txt',
    chunks_count: 5,
    created_at: '2026-04-11T09:30:00Z',
    metadata: {},
  },
  {
    id: 'doc-unknown',
    title: 'Mystery File',
    source: 'misc/file.unknown',
    chunks_count: 6,
    created_at: '2026-04-10T09:30:00Z',
    metadata: {},
  },
]

function listResponse(documents: Document[], overrides: Partial<DocumentListResponse> = {}): DocumentListResponse {
  return {
    documents,
    total: documents.length,
    page: 1,
    page_size: 50,
    total_pages: 1,
    ...overrides,
  }
}

function rebuildStatusFixture(
  overrides: Partial<MetadataRebuildStatus> = {}
): MetadataRebuildStatus {
  return {
    status: 'completed',
    started_at: '2026-04-18T08:00:00Z',
    total: 2,
    processed: 2,
    updated: 1,
    progress_percent: 100,
    ...overrides,
  }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <DocumentsPage />
    </MemoryRouter>
  )
}

describe('DocumentsPage', () => {
  beforeEach(() => {
    listMock.mockReset()
    getFoldersMock.mockReset()
    deleteMock.mockReset()
    getMetadataRebuildStatusMock.mockReset()
    startMetadataRebuildMock.mockReset()

    getFoldersMock.mockResolvedValue(foldersFixture)
    listMock.mockResolvedValue(listResponse(rootDocuments))
    getMetadataRebuildStatusMock.mockResolvedValue({
      status: rebuildStatusFixture(),
      running: false,
    })
    startMetadataRebuildMock.mockResolvedValue({
      success: true,
      status: rebuildStatusFixture({
        status: 'running',
        processed: 0,
        updated: 0,
        progress_percent: 20,
      }),
    })
    deleteMock.mockResolvedValue(undefined)

    vi.stubGlobal('confirm', vi.fn(() => true))
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('renders folders, documents, and completed rebuild status on initial load', async () => {
    renderPage()

    expect(await screen.findByText('Quarterly Report')).toBeInTheDocument()
    expect(getFoldersMock).toHaveBeenCalledTimes(1)
    expect(listMock).toHaveBeenCalledWith(1, 50, undefined, undefined, true, 'modified', 'desc')
    expect(screen.getByTestId('folder-tree')).toHaveTextContent('reports')
    expect(screen.getByTestId('folder-tree')).toHaveTextContent('media')
    expect(screen.getByText('Rebuild complete: 1 docs updated')).toBeInTheDocument()
    expect(screen.getByText((content) => content.includes('2 files'))).toBeInTheDocument()
    expect(screen.getByText('Page 1 of 1')).toBeInTheDocument()
    expect(screen.getByText('0 chunks')).toBeInTheDocument()
    expect(screen.getByText('3 chunks')).toBeInTheDocument()
  })

  it('supports debounced search and clearing the search term', async () => {
    const user = userEvent.setup()
    getMetadataRebuildStatusMock.mockResolvedValue({ status: null, running: false })
    listMock
      .mockResolvedValueOnce(listResponse(rootDocuments))
      .mockResolvedValueOnce(listResponse([], { total: 0 }))
      .mockResolvedValueOnce(listResponse(rootDocuments))

    renderPage()

    expect(await screen.findByText('Quarterly Report')).toBeInTheDocument()

    const searchInput = screen.getByPlaceholderText('Search documents...') as HTMLInputElement
    await user.type(searchInput, 'budget')

    await waitFor(() => {
      expect(listMock).toHaveBeenLastCalledWith(1, 50, undefined, 'budget', false, 'modified', 'desc')
    }, { timeout: 2000 })
    expect(screen.getByText('No documents match your search')).toBeInTheDocument()
    expect(screen.getByText('Try different keywords')).toBeInTheDocument()

    const clearButton = searchInput.parentElement?.querySelector('button')
    expect(clearButton).not.toBeNull()
    fireEvent.click(clearButton as HTMLButtonElement)

    await waitFor(() => {
      expect(listMock).toHaveBeenLastCalledWith(1, 50, undefined, undefined, true, 'modified', 'desc')
    }, { timeout: 2000 })
    expect(screen.getByText('Quarterly Report')).toBeInTheDocument()
  })

  it('navigates folders, switches view modes, and paginates within a folder', async () => {
    const user = userEvent.setup()
    getMetadataRebuildStatusMock.mockResolvedValue({ status: null, running: false })
    listMock.mockImplementation(
      (
        page: number,
        _pageSize: number,
        folder?: string
      ): Promise<DocumentListResponse> => {
        if (folder === 'reports' && page === 2) {
          return Promise.resolve(listResponse(pageTwoDocuments, { page: 2, total_pages: 2, total: 2 }))
        }
        if (folder === 'reports') {
          return Promise.resolve(listResponse(reportsDocuments, { total_pages: 2, total: 2 }))
        }
        return Promise.resolve(listResponse(rootDocuments))
      }
    )

    renderPage()

    expect(await screen.findByText('Quarterly Report')).toBeInTheDocument()

    const reportsButton = Array.from(screen.getByTestId('folder-tree').querySelectorAll('button'))
      .find((button) => button.textContent === 'reports') as HTMLButtonElement | undefined
    expect(reportsButton).toBeDefined()
    await user.click(reportsButton!)

    await waitFor(() => {
      expect(listMock).toHaveBeenLastCalledWith(1, 50, 'reports', undefined, true, 'modified', 'desc')
    })
    expect(screen.getByText('Reports Index')).toBeInTheDocument()
    expect(screen.getByText((content) => content.includes('in reports'))).toBeInTheDocument()

    await user.click(screen.getByTitle('Grid view'))
    expect(screen.getByText('2026')).toBeInTheDocument()
    expect(screen.getByText('Reports Index')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Next' }))

    await waitFor(() => {
      expect(listMock).toHaveBeenLastCalledWith(2, 50, 'reports', undefined, true, 'modified', 'desc')
    })
    expect(screen.getByText('Page Two Report')).toBeInTheDocument()
    expect(screen.getAllByText('Page 2 of 2').length).toBeGreaterThan(0)
  })

  it('starts metadata rebuilds and deletes documents after confirmation', async () => {
    const user = userEvent.setup()
    getMetadataRebuildStatusMock.mockResolvedValue({ status: null, running: false })
    listMock
      .mockResolvedValueOnce(listResponse(rootDocuments))
      .mockResolvedValueOnce(listResponse(rootDocuments))

    renderPage()

    expect(await screen.findByText('Fix 1 docs')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Fix 1 docs/i }))
    expect(startMetadataRebuildMock).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Rebuilding... 20%')).toBeInTheDocument()

    await user.click(screen.getAllByTitle('Delete')[0])

    expect(confirm).toHaveBeenCalledWith('Are you sure you want to delete this document and all its chunks?')
    expect(deleteMock).toHaveBeenCalledWith('doc-1')
    await waitFor(() => {
      expect(listMock).toHaveBeenCalledTimes(2)
    })
  })

  it('cycles sort state, refreshes data, and toggles the folder sidebar', async () => {
    const user = userEvent.setup()
    getMetadataRebuildStatusMock.mockResolvedValue({ status: null, running: false })
    listMock.mockResolvedValue(listResponse(rootDocuments))

    renderPage()

    expect(await screen.findByText('Quarterly Report')).toBeInTheDocument()

    await user.click(screen.getAllByText('Name')[1])
    await waitFor(() => {
      expect(listMock).toHaveBeenLastCalledWith(1, 50, undefined, undefined, true, 'name', 'desc')
    })

    await user.click(screen.getAllByText('Name')[1])
    await waitFor(() => {
      expect(listMock).toHaveBeenLastCalledWith(1, 50, undefined, undefined, true, 'name', 'asc')
    })
    expect(screen.getByTitle('Sort ascending')).toBeInTheDocument()

    await user.click(screen.getByTitle('Sort ascending'))
    await waitFor(() => {
      expect(listMock).toHaveBeenLastCalledWith(1, 50, undefined, undefined, true, 'name', 'desc')
    })

    await user.click(screen.getByTitle('Refresh'))
    await waitFor(() => {
      expect(getFoldersMock).toHaveBeenCalledTimes(2)
      expect(listMock.mock.calls.length).toBeGreaterThan(3)
    })

    const collapseButton = screen
      .getByText('Folders')
      .parentElement?.querySelector('button') as HTMLButtonElement | null
    expect(collapseButton).not.toBeNull()
    await user.click(collapseButton!)

    expect(screen.getByTitle('Show folders')).toBeInTheDocument()
    await user.click(screen.getByTitle('Show folders'))
    expect(screen.getByText('Folders')).toBeInTheDocument()
  })

  it('renders alternate file types and surfaces rebuild and delete failures', async () => {
    const user = userEvent.setup()
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    getFoldersMock.mockResolvedValue({
      folders: [
        { path: 'orphan/child', name: 'child', depth: 1, count: 1 },
        { path: 'solo', name: 'solo', depth: 0, count: 0 },
      ],
      total_folders: 2,
      total_documents: variedDocuments.length,
    })
    listMock.mockResolvedValue(listResponse(variedDocuments, { total: variedDocuments.length }))
    startMetadataRebuildMock.mockResolvedValueOnce({
      success: false,
      message: 'No rebuild today',
    })
    deleteMock.mockRejectedValueOnce(new Error('delete failed'))

    renderPage()

    expect(await screen.findByText('Audio Clip')).toBeInTheDocument()
    expect(screen.getByText('Video Tour')).toBeInTheDocument()
    expect(screen.getByText('Word Plan')).toBeInTheDocument()
    expect(screen.getByText('Budget Sheet')).toBeInTheDocument()
    expect(screen.getByText('Launch Deck')).toBeInTheDocument()
    expect(screen.getByText('Plain Notes')).toBeInTheDocument()
    expect(screen.getByText('Mystery File')).toBeInTheDocument()
    expect(screen.getAllByText('N/A').length).toBeGreaterThanOrEqual(2)

    await user.click(screen.getByRole('button', { name: /Fix 1 docs/i }))
    expect(await screen.findByText('No rebuild today')).toBeInTheDocument()

    await user.click(screen.getAllByTitle('Delete')[0])
    expect(deleteMock).toHaveBeenCalledWith('doc-audio')
    expect(await screen.findByText('Failed to delete document.')).toBeInTheDocument()

    await user.click(screen.getByTitle('Grid view'))
    await user.click(screen.getByText('Audio Clip'))
    expect(screen.getByText('Audio Clip')).toBeInTheDocument()

    consoleErrorSpy.mockRestore()
  })

  it('shows an error banner when document loading fails', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    listMock.mockRejectedValue(new Error('boom'))
    getMetadataRebuildStatusMock.mockResolvedValue({ status: null, running: false })

    renderPage()

    expect(await screen.findByText('Failed to load documents.')).toBeInTheDocument()

    consoleErrorSpy.mockRestore()
  })
})

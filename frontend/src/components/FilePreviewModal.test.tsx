import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import FilePreviewModal from './FilePreviewModal'

const fetchMock = vi.fn()
const convertToHtmlMock = vi.fn()
const xlsxReadMock = vi.fn()
const sheetToHtmlMock = vi.fn()
const windowOpenMock = vi.fn()

vi.mock('mammoth', () => ({
  default: {
    convertToHtml: (...args: unknown[]) => convertToHtmlMock(...args),
  },
  convertToHtml: (...args: unknown[]) => convertToHtmlMock(...args),
}))

vi.mock('xlsx', () => ({
  default: {
    read: (...args: unknown[]) => xlsxReadMock(...args),
    utils: {
      sheet_to_html: (...args: unknown[]) => sheetToHtmlMock(...args),
    },
  },
  read: (...args: unknown[]) => xlsxReadMock(...args),
  utils: {
    sheet_to_html: (...args: unknown[]) => sheetToHtmlMock(...args),
  },
}))

describe('FilePreviewModal', () => {
  beforeEach(() => {
    fetchMock.mockReset()
    convertToHtmlMock.mockReset()
    xlsxReadMock.mockReset()
    sheetToHtmlMock.mockReset()
    windowOpenMock.mockReset()

    vi.stubGlobal('fetch', fetchMock)
    Object.defineProperty(window, 'open', {
      configurable: true,
      writable: true,
      value: windowOpenMock,
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns null when closed', () => {
    const { container } = render(
      <FilePreviewModal
        isOpen={false}
        onClose={vi.fn()}
        fileUrl="/api/files/1"
        filename="report.pdf"
      />
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('renders a text preview, opens download actions, and closes on Escape or overlay click', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      text: async () => 'Plain text content',
    })
    const onClose = vi.fn()
    const user = userEvent.setup()

    render(
      <FilePreviewModal
        isOpen
        onClose={onClose}
        fileUrl="/api/files/2"
        filename="notes.txt"
      />
    )

    expect(document.body.style.overflow).toBe('hidden')
    expect(await screen.findByText('Plain text content')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/files/2?inline=true')

    await user.click(screen.getByRole('button', { name: 'Open in new tab' }))
    expect(windowOpenMock).toHaveBeenCalledWith('/api/files/2?inline=true', '_blank')

    await user.click(screen.getByRole('button', { name: 'Download' }))
    expect(windowOpenMock).toHaveBeenCalledWith('/api/files/2', '_blank')

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)

    const overlay = screen.getByRole('heading', { name: 'notes.txt' }).closest('[class*="fixed"]')
    expect(overlay).not.toBeNull()
    fireEvent.click(overlay as HTMLElement, { target: overlay })
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('renders markdown content through ReactMarkdown', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      text: async () => '# Heading\n\nBody text',
    })

    render(
      <FilePreviewModal
        isOpen
        onClose={vi.fn()}
        fileUrl="/api/files/3"
        filename="README.md"
      />
    )

    expect(await screen.findByRole('heading', { name: 'Heading' })).toBeInTheDocument()
    expect(screen.getByText('Body text')).toBeInTheDocument()
  })

  it('renders code previews in a preformatted block', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      text: async () => 'const answer = 42;',
    })

    const { container } = render(
      <FilePreviewModal
        isOpen
        onClose={vi.fn()}
        fileUrl="/api/files/4"
        filename="answer.ts"
      />
    )

    expect(await screen.findByText('const answer = 42;')).toBeInTheDocument()
    expect(container.querySelector('pre')).not.toBeNull()
  })

  it('falls back to extracted text for unknown file types and shows a download button when no fallback exists', async () => {
    const user = userEvent.setup()

    const { rerender } = render(
      <FilePreviewModal
        isOpen
        onClose={vi.fn()}
        fileUrl="/api/files/5"
        filename="archive.bin"
        fallbackContent="Recovered binary text"
      />
    )

    expect(await screen.findByText(/No native preview available for \.bin files/)).toBeInTheDocument()
    expect(screen.getByText('Recovered binary text')).toBeInTheDocument()

    rerender(
      <FilePreviewModal
        isOpen
        onClose={vi.fn()}
        fileUrl="/api/files/6"
        filename="archive.bin"
      />
    )

    await user.click(await screen.findByRole('button', { name: 'Download File' }))
    expect(windowOpenMock).toHaveBeenCalledWith('/api/files/6', '_blank')
  })

  it('renders direct media previews for pdf, image, audio, and video files', async () => {
    const { rerender, container } = render(
      <FilePreviewModal
        isOpen
        onClose={vi.fn()}
        fileUrl="/api/files/7"
        filename="report.pdf"
      />
    )

    expect(container.querySelector('iframe')).toHaveAttribute('src', '/api/files/7?inline=true')

    rerender(
      <FilePreviewModal
        isOpen
        onClose={vi.fn()}
        fileUrl="/api/files/8"
        filename="photo.png"
      />
    )
    const image = screen.getByAltText('photo.png')
    expect(image).toHaveAttribute('src', '/api/files/8?inline=true')
    fireEvent.error(image)
    expect(await screen.findByText('Failed to load image')).toBeInTheDocument()

    rerender(
      <FilePreviewModal
        isOpen
        onClose={vi.fn()}
        fileUrl="/api/files/9"
        filename="voice.mp3"
      />
    )
    expect(container.querySelector('audio source')).toHaveAttribute('src', '/api/files/9?inline=true')

    rerender(
      <FilePreviewModal
        isOpen
        onClose={vi.fn()}
        fileUrl="/api/files/10"
        filename="clip.mp4"
      />
    )
    expect(container.querySelector('video source')).toHaveAttribute('src', '/api/files/10?inline=true')
  })

  it('converts DOCX files with mammoth and shows fallback text when conversion fails', async () => {
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        arrayBuffer: async () => new ArrayBuffer(8),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
      })
    convertToHtmlMock
      .mockResolvedValueOnce({ value: '<p>Converted document</p>' })
      .mockRejectedValueOnce(new Error('bad doc'))
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    const { rerender } = render(
      <FilePreviewModal
        isOpen
        onClose={vi.fn()}
        fileUrl="/api/files/11"
        filename="proposal.docx"
      />
    )

    expect(await screen.findByText('Converted document')).toBeInTheDocument()
    expect(convertToHtmlMock).toHaveBeenCalled()

    rerender(
      <FilePreviewModal
        isOpen
        onClose={vi.fn()}
        fileUrl="/api/files/12"
        filename="proposal.docx"
        fallbackContent="Fallback text for docx"
      />
    )

    expect(await screen.findByText('Failed to render DOCX preview')).toBeInTheDocument()
    expect(screen.getByText('Fallback text for docx')).toBeInTheDocument()

    consoleErrorSpy.mockRestore()
  })

  it('converts spreadsheets to HTML and shows an error when XLSX loading fails', async () => {
    fetchMock
      .mockResolvedValueOnce({
        ok: true,
        arrayBuffer: async () => new ArrayBuffer(16),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 404,
      })
    xlsxReadMock.mockReturnValue({
      SheetNames: ['Sheet1'],
      Sheets: {
        Sheet1: { A1: { v: 'Budget' } },
      },
    })
    sheetToHtmlMock.mockReturnValue('<table><tbody><tr><td>Budget</td></tr></tbody></table>')
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    const { rerender } = render(
      <FilePreviewModal
        isOpen
        onClose={vi.fn()}
        fileUrl="/api/files/13"
        filename="budget.xlsx"
      />
    )

    expect(await screen.findByText('Budget')).toBeInTheDocument()
    expect(xlsxReadMock).toHaveBeenCalled()
    expect(sheetToHtmlMock).toHaveBeenCalled()

    rerender(
      <FilePreviewModal
        isOpen
        onClose={vi.fn()}
        fileUrl="/api/files/14"
        filename="budget.xlsx"
      />
    )

    expect(await screen.findByText('Failed to render spreadsheet preview')).toBeInTheDocument()

    consoleErrorSpy.mockRestore()
  })

  it('shows text fetch errors and uses fallback content for unsupported preview failures', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 403,
    })
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <FilePreviewModal
        isOpen
        onClose={vi.fn()}
        fileUrl="/api/files/15"
        filename="secrets.env"
        fallbackContent="TOKEN=secret"
      />
    )

    expect(await screen.findByText('Failed to load text content')).toBeInTheDocument()
    expect(screen.getByText('Showing extracted text content instead:')).toBeInTheDocument()
    expect(screen.getByText('TOKEN=secret')).toBeInTheDocument()

    consoleErrorSpy.mockRestore()
  })
})

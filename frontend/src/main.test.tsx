import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const render = vi.fn()
const createRoot = vi.fn(() => ({ render }))

vi.mock('react-dom/client', () => ({
  default: { createRoot },
}))

vi.mock('./App', () => ({
  default: () => <div>App Shell</div>,
}))

vi.mock('./contexts/TenantContext', () => ({
  TenantProvider: ({ children }: { children: ReactNode }) => (
    <div data-testid="tenant-provider">{children}</div>
  ),
}))

describe('main entrypoint', () => {
  beforeEach(() => {
    vi.resetModules()
    createRoot.mockClear()
    render.mockClear()
    document.body.innerHTML = '<div id="root"></div>'
  })

  it('creates the app root and renders the top-level providers', async () => {
    await import('./main')

    const rootElement = document.getElementById('root')
    expect(createRoot).toHaveBeenCalledWith(rootElement)
    expect(render).toHaveBeenCalledTimes(1)
  })
})

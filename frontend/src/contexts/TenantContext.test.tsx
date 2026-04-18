import { render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

import { TenantProvider, useTenant } from './TenantContext'

const {
  getTenantConfigMock,
  injectTenantCSSVariablesMock,
  updateFaviconMock,
  updateDocumentTitleMock,
  loadTenantFontsMock,
  applyI18nOverridesMock,
} = vi.hoisted(() => ({
  getTenantConfigMock: vi.fn(),
  injectTenantCSSVariablesMock: vi.fn(),
  updateFaviconMock: vi.fn(),
  updateDocumentTitleMock: vi.fn(),
  loadTenantFontsMock: vi.fn(),
  applyI18nOverridesMock: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'en' },
  }),
}))

vi.mock('../tenants/configs', () => ({
  tenantConfigs: { recallhub: {}, quellex: {} },
  getTenantConfig: getTenantConfigMock,
}))

vi.mock('../tenants/utils', () => ({
  injectTenantCSSVariables: injectTenantCSSVariablesMock,
  updateFavicon: updateFaviconMock,
  updateDocumentTitle: updateDocumentTitleMock,
  loadTenantFonts: loadTenantFontsMock,
  applyI18nOverrides: applyI18nOverridesMock,
}))

const baseTenant = {
  theme: { fonts: { heading: 'Fraunces', body: 'Manrope', mono: 'Fira Code' } },
  branding: { appName: 'RecallHub', faviconUrl: '/favicon.ico' },
  content: { i18nOverrides: {} },
  features: {},
}

function TenantHarness() {
  const { tenantId, isLoading, tenant } = useTenant()
  return (
    <div>
      <div>{tenantId}</div>
      <div>{String(isLoading)}</div>
      <div>{tenant.branding.appName}</div>
    </div>
  )
}

describe('TenantContext', () => {
  beforeEach(() => {
    getTenantConfigMock.mockReset()
    injectTenantCSSVariablesMock.mockReset()
    updateFaviconMock.mockReset()
    updateDocumentTitleMock.mockReset()
    loadTenantFontsMock.mockReset()
    applyI18nOverridesMock.mockReset()
    global.fetch = vi.fn()

    getTenantConfigMock.mockImplementation((tenantId: string) => ({
      ...baseTenant,
      branding: {
        appName: tenantId === 'quellex' ? 'Quellex' : 'RecallHub',
        faviconUrl: '/favicon.ico',
      },
      content: {
        i18nOverrides: tenantId === 'quellex' ? { en: { welcome: 'Hello' } } : {},
      },
    }))
  })

  it('renders the build-time tenant when runtime config is unavailable', async () => {
    vi.mocked(global.fetch).mockRejectedValueOnce(new Error('offline'))

    render(
      <TenantProvider>
        <TenantHarness />
      </TenantProvider>
    )

    await waitFor(() => {
      expect(screen.getByText('recallhub')).toBeInTheDocument()
    })
    expect(screen.getByText('RecallHub')).toBeInTheDocument()
    expect(screen.getByText('false')).toBeInTheDocument()
    expect(injectTenantCSSVariablesMock).toHaveBeenCalled()
    expect(updateDocumentTitleMock).toHaveBeenCalledWith('RecallHub')
    expect(loadTenantFontsMock).toHaveBeenCalled()
    expect(applyI18nOverridesMock).not.toHaveBeenCalled()
  })

  it('applies the runtime tenant override when the backend returns a known tenant', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ tenant_id: 'quellex' }),
    } as Response)

    render(
      <TenantProvider>
        <TenantHarness />
      </TenantProvider>
    )

    await waitFor(() => {
      expect(screen.getByText('quellex')).toBeInTheDocument()
    })
    expect(screen.getByText('Quellex')).toBeInTheDocument()
    expect(updateDocumentTitleMock).toHaveBeenCalledWith('Quellex')
    expect(updateFaviconMock).toHaveBeenCalledWith('/favicon.ico')
    expect(applyI18nOverridesMock).toHaveBeenCalled()
  })
})

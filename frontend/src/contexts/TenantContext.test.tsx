import { render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

import { TenantProvider, applyRuntimeOverrides, useTenant } from './TenantContext'

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

  it('switches requireAuth on when the backend enforces authentication', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ tenant_id: 'recallhub', require_auth: true }),
    } as Response)

    function FeatureHarness() {
      const { tenant, isLoading } = useTenant()
      return <div>{isLoading ? 'loading' : `requireAuth=${String(tenant.features.requireAuth)}`}</div>
    }

    render(
      <TenantProvider>
        <FeatureHarness />
      </TenantProvider>
    )

    await waitFor(() => {
      expect(screen.getByText('requireAuth=true')).toBeInTheDocument()
    })
  })

  it('never relaxes a tenant that already requires authentication', () => {
    const config = {
      ...baseTenant,
      features: { requireAuth: true, startPage: 'login' },
    } as unknown as Parameters<typeof applyRuntimeOverrides>[0]

    expect(applyRuntimeOverrides(config, { require_auth: false })).toBe(config)
    expect(applyRuntimeOverrides(config, {})).toBe(config)

    const guestConfig = {
      ...baseTenant,
      features: { requireAuth: false, startPage: 'landing' },
    } as unknown as Parameters<typeof applyRuntimeOverrides>[0]

    expect(applyRuntimeOverrides(guestConfig, { require_auth: false })).toBe(guestConfig)
    expect(applyRuntimeOverrides(guestConfig, { require_auth: 'yes' })).toBe(guestConfig)
    expect(applyRuntimeOverrides(guestConfig, { require_auth: true }).features).toEqual({
      requireAuth: true,
      startPage: 'landing',
    })
  })
})

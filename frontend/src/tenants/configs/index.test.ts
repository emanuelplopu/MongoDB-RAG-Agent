import { describe, expect, it, vi } from 'vitest'

import { getTenantConfig, tenantConfigs, DEFAULT_TENANT_ID } from './index'
import { quellexConfig } from './quellex'
import { recallhubConfig } from './recallhub'

describe('tenant configs', () => {
  it('exports the known tenant configurations', () => {
    expect(DEFAULT_TENANT_ID).toBe('recallhub')
    expect(tenantConfigs.recallhub).toBe(recallhubConfig)
    expect(tenantConfigs.quellex).toBe(quellexConfig)
    expect(recallhubConfig.branding.appName).toBe('RecallHub')
    expect(recallhubConfig.features.showEmbeddingBenchmark).toBe(true)
    expect(quellexConfig.branding.appName).toBe('Quellex')
    expect(quellexConfig.features.landingPageVariant).toBe('quellex')
    expect(quellexConfig.content.i18nOverrides.de['chatPage.assistantTitle']).toBe('Quellex-Assistent')
  })

  it('returns the requested config and falls back for unknown tenants', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

    expect(getTenantConfig('quellex')).toBe(quellexConfig)
    expect(getTenantConfig('unknown-tenant')).toBe(recallhubConfig)
    expect(warn).toHaveBeenCalledWith(
      '[Tenant] Unknown tenant ID "unknown-tenant", falling back to "recallhub".'
    )
  })
})

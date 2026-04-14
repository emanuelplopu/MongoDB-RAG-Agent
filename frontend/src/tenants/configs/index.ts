import { TenantConfig } from '../types'
import { recallhubConfig } from './recallhub'
import { quellexConfig } from './quellex'

export const tenantConfigs: Record<string, TenantConfig> = {
  recallhub: recallhubConfig,
  quellex: quellexConfig,
}

export const DEFAULT_TENANT_ID = 'recallhub'

export function getTenantConfig(tenantId: string): TenantConfig {
  if (!(tenantId in tenantConfigs)) {
    console.warn(`[Tenant] Unknown tenant ID "${tenantId}", falling back to "${DEFAULT_TENANT_ID}".`)
  }
  return tenantConfigs[tenantId] || tenantConfigs[DEFAULT_TENANT_ID]
}

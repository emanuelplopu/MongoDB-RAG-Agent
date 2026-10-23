import { createContext, useContext } from 'react'
import { TenantConfig } from '../tenants/types'

export interface TenantContextType {
  tenant: TenantConfig
  tenantId: string
  isLoading: boolean
}

/** Provided by `TenantProvider` (see ./TenantProvider.tsx). */
export const TenantContext = createContext<TenantContextType | undefined>(undefined)

export interface RuntimeTenantConfig {
  tenant_id?: unknown
  /** Mirrors the backend REQUIRE_AUTH setting (see backend/core/config.py). */
  require_auth?: unknown
}

/**
 * Merge backend-controlled security flags into the static tenant config.
 * The backend can only tighten the rules: when it enforces authentication the
 * frontend must guard its routes too, otherwise guests would see pages whose
 * every request fails with 401.
 */
export function applyRuntimeOverrides(config: TenantConfig, runtime: RuntimeTenantConfig): TenantConfig {
  const backendRequiresAuth = runtime.require_auth === true
  if (!backendRequiresAuth || config.features.requireAuth) {
    return config
  }
  return {
    ...config,
    features: { ...config.features, requireAuth: true },
  }
}

export function useTenant() {
  const context = useContext(TenantContext)
  if (context === undefined) {
    throw new Error('useTenant must be used within a TenantProvider')
  }
  return context
}

import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { TenantConfig } from '../tenants/types'
import { getTenantConfig, tenantConfigs } from '../tenants/configs'
import {
  injectTenantCSSVariables,
  updateFavicon,
  updateDocumentTitle,
  loadTenantFonts,
  applyI18nOverrides,
} from '../tenants/utils'

interface TenantContextType {
  tenant: TenantConfig
  tenantId: string
  isLoading: boolean
}

const TenantContext = createContext<TenantContextType | undefined>(undefined)

function resolveTenantId(): string {
  // 1. Build-time env var
  const envTenant = import.meta.env.VITE_TENANT
  if (envTenant && typeof envTenant === 'string') {
    return envTenant
  }
  // 2. Default fallback
  return 'recallhub'
}

interface RuntimeTenantConfig {
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

export function TenantProvider({ children }: { children: ReactNode }) {
  const buildTimeTenantId = resolveTenantId()
  const [tenant, setTenant] = useState<TenantConfig>(() => getTenantConfig(buildTimeTenantId))
  const [tenantId, setTenantId] = useState(buildTimeTenantId)
  const [isLoading, setIsLoading] = useState(true)
  const { i18n } = useTranslation()

  // Try to fetch runtime config from backend (optional override)
  useEffect(() => {
    let cancelled = false

    async function fetchRuntimeConfig() {
      try {
        const response = await fetch('/api/v1/tenant/config')
        if (response.ok) {
          const data = (await response.json()) as RuntimeTenantConfig
          if (!cancelled && typeof data.tenant_id === 'string' && data.tenant_id) {
            const runtimeTenantId = data.tenant_id
            if (!(runtimeTenantId in tenantConfigs)) {
              console.warn(
                `[Tenant] Backend returned tenant ID "${runtimeTenantId}" which has no frontend config. ` +
                `Falling back to build-time tenant "${buildTimeTenantId}".`
              )
              setTenant(current => applyRuntimeOverrides(current, data))
            } else {
              const runtimeConfig = applyRuntimeOverrides(getTenantConfig(runtimeTenantId), data)
              setTenantId(runtimeTenantId)
              setTenant(runtimeConfig)
            }
          }
        }
      } catch {
        // Runtime config unavailable, use build-time config (already set)
        console.debug('[Tenant] Backend config endpoint unavailable, using build-time config.')
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    }

    fetchRuntimeConfig()
    return () => { cancelled = true }
  }, [])

  // Apply tenant theming whenever tenant changes
  useEffect(() => {
    // Inject CSS variables
    injectTenantCSSVariables(tenant.theme)

    // Update favicon and title
    if (tenant.branding.faviconUrl) {
      updateFavicon(tenant.branding.faviconUrl)
    }
    updateDocumentTitle(tenant.branding.appName)

    // Load tenant fonts
    loadTenantFonts(tenant.theme.fonts)

    // Apply i18n overrides
    if (Object.keys(tenant.content.i18nOverrides).length > 0) {
      applyI18nOverrides(i18n, tenant.content.i18nOverrides)
    }
  }, [tenant, i18n])

  return (
    <TenantContext.Provider value={{ tenant, tenantId, isLoading }}>
      {isLoading ? (
        <div className="flex items-center justify-center min-h-screen bg-background dark:bg-gray-900">
          <div className="animate-spin h-8 w-8 border-4 border-primary border-t-transparent rounded-full" />
        </div>
      ) : (
        children
      )}
    </TenantContext.Provider>
  )
}

export function useTenant() {
  const context = useContext(TenantContext)
  if (context === undefined) {
    throw new Error('useTenant must be used within a TenantProvider')
  }
  return context
}

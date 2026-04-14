import { Navigate } from 'react-router-dom'
import { useTenant } from '../contexts/TenantContext'
import { TenantFeatures } from '../tenants/types'

interface FeatureGuardProps {
  /** The feature flag key from TenantFeatures to check */
  feature: keyof TenantFeatures
  children: React.ReactNode
}

/**
 * Route guard that redirects to the dashboard if a tenant feature is disabled.
 * Wrap feature-flagged pages to prevent direct URL access when the feature is off.
 */
export default function FeatureGuard({ feature, children }: FeatureGuardProps) {
  const { tenant } = useTenant()
  const isEnabled = tenant.features[feature]

  if (!isEnabled) {
    return <Navigate to="/dashboard" replace />
  }

  return <>{children}</>
}

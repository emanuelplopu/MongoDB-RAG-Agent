import { ReactNode } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../contexts/AuthContext'
import { useTenant } from '../contexts/TenantContext'
import { useLocalizedPath } from '../hooks/useLocalizedNavigate'

interface RequireAuthProps {
  children?: ReactNode
}

/**
 * Route guard for tenants with `features.requireAuth`.
 *
 * While the stored session is being validated it renders a spinner instead of
 * the protected page, so no API calls are fired for a user who is about to be
 * redirected. Guests are sent to the login page with the attempted location in
 * router state; LoginPage returns them there after a successful sign-in.
 *
 * Tenants that allow guests render the children unchanged.
 */
export default function RequireAuth({ children }: RequireAuthProps) {
  const { isAuthenticated, isLoading, sessionExpired } = useAuth()
  const { tenant } = useTenant()
  const location = useLocation()
  const getLocalizedPath = useLocalizedPath()
  const { t } = useTranslation()

  const content = children !== undefined ? <>{children}</> : <Outlet />

  if (!tenant.features.requireAuth) {
    return content
  }

  if (isLoading) {
    return (
      <div
        className="flex items-center justify-center min-h-screen bg-background dark:bg-gray-900"
        role="status"
        aria-live="polite"
        data-testid="auth-loading"
      >
        <div className="text-center">
          <div className="animate-spin h-10 w-10 border-4 border-primary border-t-transparent rounded-full mx-auto mb-4" />
          <p className="text-secondary dark:text-gray-400">{t('common.loading')}</p>
        </div>
      </div>
    )
  }

  if (!isAuthenticated) {
    const from = `${location.pathname}${location.search}${location.hash}`
    return (
      <Navigate
        to={getLocalizedPath('/login')}
        replace
        state={{ from, sessionExpired }}
      />
    )
  }

  return content
}

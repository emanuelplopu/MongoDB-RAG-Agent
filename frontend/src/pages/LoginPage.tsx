import { useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../contexts/AuthContext'
import { useTenant } from '../contexts/TenantContext'
import { ExclamationCircleIcon, InformationCircleIcon } from '@heroicons/react/24/outline'
import { ApiError } from '../api/client'
import { useLocalizedNavigate, useLocalizedPath } from '../components/LocalizedLink'
import TenantLogo from '../components/TenantLogo'
import ThemeSwitcher from '../components/ThemeSwitcher'
import LanguageSwitcher from '../components/LanguageSwitcher'

interface LoginLocationState {
  /** Path the user tried to open before RequireAuth redirected here. */
  from?: string
  /** Set by RequireAuth when the redirect was caused by an expired session. */
  sessionExpired?: boolean
}

/** Only in-app paths are honoured as post-login targets (no open redirects). */
function resolveRedirectTarget(state: LoginLocationState | null): string {
  const from = state?.from
  if (typeof from === 'string' && from.startsWith('/') && !from.startsWith('//')) {
    // Never bounce back to the login page itself
    if (!/^\/([a-z]{2}\/)?login(\/|$|\?|#)/.test(from)) {
      return from
    }
  }
  return '/dashboard'
}

export default function LoginPage() {
  const [isLogin, setIsLogin] = useState(true)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [titlePrefix, setTitlePrefix] = useState('')
  const [titleSuffix, setTitleSuffix] = useState('')
  const [error, setError] = useState('')
  const [errorId, setErrorId] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  
  const { login, register, isAuthenticated, isLoading: isAuthLoading, sessionExpired } = useAuth()
  const { tenant } = useTenant()
  const navigate = useLocalizedNavigate()
  const getLocalizedPath = useLocalizedPath()
  const location = useLocation()
  const { t } = useTranslation()

  const locationState = (location.state ?? null) as LoginLocationState | null
  const redirectTarget = resolveRedirectTarget(locationState)
  const requireAuth = tenant.features.requireAuth
  const showSessionExpiredNotice = Boolean(sessionExpired || locationState?.sessionExpired)

  // A user who already holds a valid session never sees the login form:
  // send them on to where they wanted to go (the dashboard by default).
  if (isAuthenticated && !isAuthLoading) {
    return <Navigate to={getLocalizedPath(redirectTarget)} replace />
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setErrorId(null)
    setIsLoading(true)

    try {
      if (isLogin) {
        await login(email, password)
      } else {
        if (!name.trim()) {
          setError(t('login.nameRequired'))
          setIsLoading(false)
          return
        }
        await register(email, name, password, titlePrefix || undefined, titleSuffix || undefined)
      }
      navigate(redirectTarget, { replace: true })
    } catch (err: any) {
      // Handle ApiError with user-friendly messages
      if (err instanceof ApiError) {
        setError(err.getUserMessage())
        setErrorId(err.errorId || null)
      } else if (err?.status === 401) {
        // Authentication failed - show specific message
        setError(t('login.invalidCredentials'))
      } else if (err?.status >= 500) {
        // Server error - show error ID if available
        setError(t('login.serverError'))
        setErrorId(err?.errorId || null)
      } else {
        setError(err.message || t('login.authFailed'))
      }
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-background dark:bg-gray-900 flex items-center justify-center px-4">
      {/* Top Bar with Language and Theme Switchers */}
      <div className="absolute top-4 right-4 z-50 flex items-center gap-2">
        <LanguageSwitcher />
        <ThemeSwitcher />
      </div>

      <div className="max-w-md w-full">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center mb-4">
            <TenantLogo size="md" />
          </div>
          <h1 className="text-2xl font-display font-bold text-primary-900 dark:text-white">{t('common.appName')}</h1>
          <p className="text-primary-700 dark:text-gray-400 mt-2">
            {isLogin ? t('login.signInTitle') : t('login.signUpTitle')}
          </p>
        </div>

        {/* Form */}
        <div className="bg-surface dark:bg-gray-800 rounded-2xl shadow-xl p-8">
          {showSessionExpiredNotice && (
            <div
              role="status"
              className="mb-6 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-xl p-4 flex items-start gap-3"
            >
              <InformationCircleIcon className="h-5 w-5 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
              <p className="text-sm text-amber-800 dark:text-amber-200">{t('errors.sessionExpired.message')}</p>
            </div>
          )}
          <form onSubmit={handleSubmit} className="space-y-6">
            {!isLogin && (
              <div>
                <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                  {t('login.name')}
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full px-4 py-3 rounded-xl border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100 focus:border-primary focus:ring-2 focus:ring-primary/20 focus:outline-hidden"
                  placeholder={t('login.namePlaceholder')}
                  required={!isLogin}
                />
                {/* Optional title fields */}
                <div className="grid grid-cols-2 gap-3 mt-3">
                  <div>
                    <label className="block text-xs text-secondary dark:text-gray-400 mb-1">
                      {t('login.titlePrefix', 'Title (before)')}
                    </label>
                    <input
                      type="text"
                      value={titlePrefix}
                      onChange={(e) => setTitlePrefix(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100 text-sm focus:border-primary focus:ring-2 focus:ring-primary/20 focus:outline-hidden"
                      placeholder="Mag., Dr., RA"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-secondary dark:text-gray-400 mb-1">
                      {t('login.titleSuffix', 'Title (after)')}
                    </label>
                    <input
                      type="text"
                      value={titleSuffix}
                      onChange={(e) => setTitleSuffix(e.target.value)}
                      className="w-full px-3 py-2 rounded-lg border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100 text-sm focus:border-primary focus:ring-2 focus:ring-primary/20 focus:outline-hidden"
                      placeholder="Rechtsanwältin, LL.M."
                    />
                  </div>
                </div>
              </div>
            )}

            <div>
              <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                {t('login.email')}
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full px-4 py-3 rounded-xl border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100 focus:border-primary focus:ring-2 focus:ring-primary/20 focus:outline-hidden"
                placeholder={t('login.emailPlaceholder')}
                required
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-primary-900 dark:text-gray-200 mb-2">
                {t('login.password')}
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full px-4 py-3 rounded-xl border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100 focus:border-primary focus:ring-2 focus:ring-primary/20 focus:outline-hidden"
                placeholder={t('login.passwordPlaceholder')}
                required
                minLength={6}
              />
            </div>

            {error && (
              <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl p-4">
                <div className="flex items-start gap-3">
                  <ExclamationCircleIcon className="h-5 w-5 text-red-500 dark:text-red-400 shrink-0 mt-0.5" />
                  <div className="flex-1">
                    <p className="text-red-600 dark:text-red-400 text-sm">{error}</p>
                    {errorId && (
                      <p className="text-red-500/70 dark:text-red-400/70 text-xs mt-1">
                        {t('login.errorId')}: {errorId}
                      </p>
                    )}
                  </div>
                </div>
              </div>
            )}

            <button
              type="submit"
              disabled={isLoading}
              className="w-full py-3 px-4 rounded-xl bg-primary text-white font-medium hover:bg-primary-700 focus:outline-hidden focus:ring-2 focus:ring-primary/50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {isLoading ? (
                <span className="flex items-center justify-center gap-2">
                  <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  {isLogin ? t('login.signingIn') : t('login.creatingAccount')}
                </span>
              ) : (
                isLogin ? t('login.signIn') : t('login.createAccount')
              )}
            </button>
          </form>

          <div className="mt-6 text-center">
            <button
              type="button"
              onClick={() => {
                setIsLogin(!isLogin)
                setError('')
              }}
              className="text-sm text-primary hover:underline"
            >
              {isLogin ? t('login.noAccount') : t('login.hasAccount')}
            </button>
          </div>
        </div>

        {/* Skip login option - not offered when the tenant requires a session */}
        {!requireAuth && (
          <div className="mt-4 text-center">
            <button
              type="button"
              onClick={() => navigate('/')}
              className="text-sm text-secondary dark:text-gray-500 hover:text-primary dark:hover:text-primary-300"
            >
              {t('login.continueWithout')}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

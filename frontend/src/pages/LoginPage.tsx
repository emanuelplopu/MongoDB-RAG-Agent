import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../contexts/AuthContext'
import { ExclamationCircleIcon } from '@heroicons/react/24/outline'
import { ApiError } from '../api/client'
import { useLocalizedNavigate } from '../components/LocalizedLink'
import ThemeSwitcher from '../components/ThemeSwitcher'
import LanguageSwitcher from '../components/LanguageSwitcher'

export default function LoginPage() {
  const [isLogin, setIsLogin] = useState(true)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [errorId, setErrorId] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  
  const { login, register } = useAuth()
  const navigate = useLocalizedNavigate()
  const { t } = useTranslation()

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
        await register(email, name, password)
      }
      navigate('/dashboard')
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
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-indigo-500 to-purple-600 mb-4">
            <svg className="h-10 w-10" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M12 10h6c2.76 0 5 2.24 5 5s-2.24 5-5 5h-4" stroke="white" strokeWidth="2.5" strokeLinecap="round" fill="none"/>
              <path d="M14 18l-3 3-3-3" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" fill="none"/>
              <path d="M11 21v2" stroke="white" strokeWidth="2.5" strokeLinecap="round"/>
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-primary-900 dark:text-white">{t('common.appName')}</h1>
          <p className="text-secondary dark:text-gray-400 mt-2">
            {isLogin ? t('login.signInTitle') : t('login.signUpTitle')}
          </p>
        </div>

        {/* Form */}
        <div className="bg-surface dark:bg-gray-800 rounded-2xl shadow-xl p-8">
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
                  className="w-full px-4 py-3 rounded-xl border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100 focus:border-primary focus:ring-2 focus:ring-primary/20 focus:outline-none"
                  placeholder={t('login.namePlaceholder')}
                  required={!isLogin}
                />
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
                className="w-full px-4 py-3 rounded-xl border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100 focus:border-primary focus:ring-2 focus:ring-primary/20 focus:outline-none"
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
                className="w-full px-4 py-3 rounded-xl border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100 focus:border-primary focus:ring-2 focus:ring-primary/20 focus:outline-none"
                placeholder={t('login.passwordPlaceholder')}
                required
                minLength={6}
              />
            </div>

            {error && (
              <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl p-4">
                <div className="flex items-start gap-3">
                  <ExclamationCircleIcon className="h-5 w-5 text-red-500 dark:text-red-400 flex-shrink-0 mt-0.5" />
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
              className="w-full py-3 px-4 rounded-xl bg-primary text-white font-medium hover:bg-primary-700 focus:outline-none focus:ring-2 focus:ring-primary/50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
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

        {/* Skip login option */}
        <div className="mt-4 text-center">
          <button
            type="button"
            onClick={() => navigate('/')}
            className="text-sm text-secondary dark:text-gray-500 hover:text-primary dark:hover:text-primary-300"
          >
            {t('login.continueWithout')}
          </button>
        </div>
      </div>
    </div>
  )
}

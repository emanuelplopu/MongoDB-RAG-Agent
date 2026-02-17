import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { ExclamationTriangleIcon, ArrowRightOnRectangleIcon, UserIcon } from '@heroicons/react/24/outline'
import { useLocalizedNavigate } from './LocalizedLink'

interface SessionExpiredModalProps {
  isOpen: boolean
  onClose: () => void
  onContinueAsGuest: () => void
}

export default function SessionExpiredModal({ isOpen, onClose, onContinueAsGuest }: SessionExpiredModalProps) {
  const navigate = useLocalizedNavigate()
  const { t } = useTranslation()
  const [isLoading, setIsLoading] = useState(false)
  const [isVisible, setIsVisible] = useState(false)
  const [isAnimating, setIsAnimating] = useState(false)

  // Handle open/close animation
  useEffect(() => {
    if (isOpen) {
      setIsVisible(true)
      // Small delay for animation
      requestAnimationFrame(() => {
        setIsAnimating(true)
      })
    } else {
      setIsAnimating(false)
      const timeout = setTimeout(() => {
        setIsVisible(false)
      }, 200)
      return () => clearTimeout(timeout)
    }
  }, [isOpen])

  const handleLoginAgain = () => {
    setIsLoading(true)
    onClose()
    navigate('/login')
  }

  const handleContinueAsGuest = () => {
    onContinueAsGuest()
    onClose()
  }

  if (!isVisible) return null

  return (
    <div 
      className={`fixed inset-0 z-50 flex items-center justify-center p-4 transition-opacity duration-200 ${
        isAnimating ? 'opacity-100' : 'opacity-0'
      }`}
      role="dialog"
      aria-modal="true"
      aria-labelledby="session-expired-title"
    >
      {/* Backdrop */}
      <div 
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={() => {}} // Prevent closing by clicking backdrop
      />

      {/* Modal Panel */}
      <div 
        className={`relative w-full max-w-md transform overflow-hidden rounded-2xl bg-white dark:bg-gray-800 p-6 text-left shadow-xl transition-all duration-200 ${
          isAnimating ? 'scale-100 opacity-100' : 'scale-95 opacity-0'
        }`}
      >
        {/* Icon */}
        <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-full bg-amber-100 dark:bg-amber-900/30 mb-4">
          <ExclamationTriangleIcon className="h-10 w-10 text-amber-600 dark:text-amber-400" aria-hidden="true" />
        </div>

        {/* Title */}
        <h3
          id="session-expired-title"
          className="text-xl font-semibold leading-6 text-gray-900 dark:text-gray-100 text-center mb-2"
        >
          Session Expired
        </h3>

        {/* Description */}
        <div className="mt-2">
          <p className="text-sm text-gray-600 dark:text-gray-400 text-center">
            {t('errors.sessionExpired.message')}
          </p>
        </div>

        {/* Actions */}
        <div className="mt-6 space-y-3">
          <button
            type="button"
            disabled={isLoading}
            className="w-full inline-flex justify-center items-center gap-2 rounded-xl bg-primary px-4 py-3 text-sm font-semibold text-white shadow-sm hover:bg-primary-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            onClick={handleLoginAgain}
          >
            <ArrowRightOnRectangleIcon className="h-5 w-5" />
            {isLoading ? 'Redirecting...' : 'Log In Again'}
          </button>

          <button
            type="button"
            className="w-full inline-flex justify-center items-center gap-2 rounded-xl bg-gray-100 dark:bg-gray-700 px-4 py-3 text-sm font-semibold text-gray-900 dark:text-gray-100 shadow-sm hover:bg-gray-200 dark:hover:bg-gray-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-gray-300 transition-colors"
            onClick={handleContinueAsGuest}
          >
            <UserIcon className="h-5 w-5" />
            Continue as Guest
          </button>
        </div>

        {/* Note */}
        <p className="mt-4 text-xs text-gray-500 dark:text-gray-500 text-center">
          Guest users can browse but won't have access to personalized features.
        </p>
      </div>
    </div>
  )
}

import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { LifebuoyIcon, XMarkIcon } from '@heroicons/react/24/outline'
import { useToast } from '../contexts/ToastContext'
import { useAuth } from '../contexts/AuthContext'
import { supportApi } from '../api/client'

interface SupportRequestButtonProps {
  sessionId?: string
}

export default function SupportRequestButton({ sessionId }: SupportRequestButtonProps) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const toast = useToast()

  const [isOpen, setIsOpen] = useState(false)
  const [isVisible, setIsVisible] = useState(false)
  const [isAnimating, setIsAnimating] = useState(false)
  const [diagnosticText, setDiagnosticText] = useState('')
  const [userDescription, setUserDescription] = useState('')
  const [isLoadingDiagnostic, setIsLoadingDiagnostic] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Handle open/close animation
  useEffect(() => {
    if (isOpen) {
      setIsVisible(true)
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

  const handleOpen = useCallback(async () => {
    setIsOpen(true)
    setUserDescription('')
    setDiagnosticText('')

    if (sessionId) {
      setIsLoadingDiagnostic(true)
      try {
        const response = await supportApi.getSessionDiagnostic(sessionId)
        setDiagnosticText(response.diagnostic)
      } catch {
        setDiagnosticText('Unable to load diagnostic information.')
      } finally {
        setIsLoadingDiagnostic(false)
      }
    } else {
      setDiagnosticText('No active session. General support request.')
    }
  }, [sessionId])

  const handleClose = useCallback(() => {
    setIsOpen(false)
  }, [])

  const handleSubmit = useCallback(async () => {
    setIsSubmitting(true)
    try {
      await supportApi.submitSupportRequest({
        diagnostic_text: diagnosticText,
        session_id: sessionId,
        user_description: userDescription || undefined,
      })
      toast.success(t('support.success'))
      handleClose()
    } catch {
      toast.error(t('support.error'))
    } finally {
      setIsSubmitting(false)
    }
  }, [diagnosticText, sessionId, userDescription, toast, t, handleClose])

  // Don't render for admin users or unauthenticated users
  if (!user || user.is_admin) {
    return null
  }

  return (
    <>
      {/* Floating support button */}
      <button
        onClick={handleOpen}
        className="fixed bottom-6 left-6 z-30 p-2.5 rounded-full bg-gray-200 dark:bg-gray-700 text-gray-600 dark:text-gray-300 opacity-60 hover:opacity-100 hover:bg-primary-100 hover:text-primary dark:hover:bg-primary-900/50 dark:hover:text-primary-300 shadow-md transition-all duration-200"
        aria-label={t('support.requestSupport')}
        title={t('support.requestSupport')}
      >
        <LifebuoyIcon className="h-5 w-5" />
      </button>

      {/* Support request modal */}
      {isVisible && (
        <div
          className={`fixed inset-0 z-50 flex items-center justify-center p-4 transition-opacity duration-200 ${
            isAnimating ? 'opacity-100' : 'opacity-0'
          }`}
          role="dialog"
          aria-modal="true"
          aria-labelledby="support-request-title"
        >
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/50 backdrop-blur-sm"
            onClick={handleClose}
          />

          {/* Modal Panel */}
          <div
            className={`relative w-full max-w-lg transform overflow-hidden rounded-2xl bg-white dark:bg-gray-800 p-6 text-left shadow-xl transition-all duration-200 ${
              isAnimating ? 'scale-100 opacity-100' : 'scale-95 opacity-0'
            }`}
          >
            {/* Header */}
            <div className="flex items-center justify-between mb-4">
              <h3
                id="support-request-title"
                className="text-lg font-semibold text-gray-900 dark:text-gray-100"
              >
                {t('support.requestSupport')}
              </h3>
              <button
                onClick={handleClose}
                className="p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400 transition-colors"
                aria-label={t('common.close')}
              >
                <XMarkIcon className="h-5 w-5" />
              </button>
            </div>

            {/* Explanation */}
            <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
              {t('support.explanation')}
            </p>

            {/* Diagnostic text (read-only) */}
            <div className="mb-4">
              <textarea
                readOnly
                value={isLoadingDiagnostic ? t('support.loading') : diagnosticText}
                className="w-full h-40 p-3 text-xs font-mono bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-gray-700 dark:text-gray-300 resize-none focus:outline-none"
                rows={10}
              />
            </div>

            {/* User description */}
            <div className="mb-6">
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                {t('support.describeIssue')}
              </label>
              <textarea
                value={userDescription}
                onChange={(e) => setUserDescription(e.target.value)}
                placeholder={t('support.descriptionPlaceholder')}
                className="w-full h-24 p-3 text-sm bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 resize-none focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary"
                rows={4}
              />
            </div>

            {/* Footer buttons */}
            <div className="flex justify-end gap-3">
              <button
                onClick={handleClose}
                className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
              >
                {t('support.cancel')}
              </button>
              <button
                onClick={handleSubmit}
                disabled={isSubmitting || isLoadingDiagnostic}
                className="px-4 py-2 text-sm font-medium text-white bg-primary rounded-lg hover:bg-primary-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {isSubmitting ? t('common.loading') : t('support.submit')}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

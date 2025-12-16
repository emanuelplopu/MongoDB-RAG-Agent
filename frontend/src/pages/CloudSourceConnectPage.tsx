import { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeftIcon,
  ArrowPathIcon,
  ExclamationTriangleIcon,
  CheckCircleIcon,
  EyeIcon,
  EyeSlashIcon,
  CloudIcon,
  LockClosedIcon,
} from '@heroicons/react/24/outline'
import {
  cloudSourcesApi,
  CloudProvider,
  CloudProviderType,
} from '../api/client'
import { useAuth } from '../contexts/AuthContext'

// Provider icons mapping
const PROVIDER_ICONS: Record<CloudProviderType, string> = {
  google_drive: '🔵',
  onedrive: '☁️',
  sharepoint: '📊',
  dropbox: '📦',
  owncloud: '☁️',
  nextcloud: '🟢',
  confluence: '📝',
  jira: '🔷',
  email_imap: '✉️',
  email_gmail: '📧',
  email_outlook: '📨',
}

interface FormField {
  name: string
  label: string
  type: 'text' | 'password' | 'url' | 'email' | 'number'
  placeholder?: string
  required?: boolean
  helpText?: string
}

// Field configurations per provider
const PROVIDER_FIELDS: Partial<Record<CloudProviderType, FormField[]>> = {
  owncloud: [
    {
      name: 'server_url',
      label: 'Server URL',
      type: 'url',
      placeholder: 'https://cloud.example.com',
      required: true,
      helpText: 'The base URL of your OwnCloud/Nextcloud server',
    },
    {
      name: 'username',
      label: 'Username',
      type: 'text',
      placeholder: 'your-username',
      required: true,
    },
    {
      name: 'password',
      label: 'Password / App Token',
      type: 'password',
      placeholder: 'Enter password or app token',
      required: true,
      helpText: 'We recommend using an app password for better security',
    },
  ],
  nextcloud: [
    {
      name: 'server_url',
      label: 'Server URL',
      type: 'url',
      placeholder: 'https://nextcloud.example.com',
      required: true,
      helpText: 'The base URL of your Nextcloud server',
    },
    {
      name: 'username',
      label: 'Username',
      type: 'text',
      placeholder: 'your-username',
      required: true,
    },
    {
      name: 'password',
      label: 'App Password',
      type: 'password',
      placeholder: 'Enter app password',
      required: true,
      helpText: 'Generate an app password in Settings → Security',
    },
  ],
  confluence: [
    {
      name: 'server_url',
      label: 'Confluence URL',
      type: 'url',
      placeholder: 'https://your-domain.atlassian.net/wiki',
      required: true,
    },
    {
      name: 'username',
      label: 'Email',
      type: 'email',
      placeholder: 'your-email@company.com',
      required: true,
    },
    {
      name: 'api_key',
      label: 'API Token',
      type: 'password',
      placeholder: 'Enter Atlassian API token',
      required: true,
      helpText: 'Generate at id.atlassian.com/manage-profile/security/api-tokens',
    },
  ],
  jira: [
    {
      name: 'server_url',
      label: 'Jira URL',
      type: 'url',
      placeholder: 'https://your-domain.atlassian.net',
      required: true,
    },
    {
      name: 'username',
      label: 'Email',
      type: 'email',
      placeholder: 'your-email@company.com',
      required: true,
    },
    {
      name: 'api_key',
      label: 'API Token',
      type: 'password',
      placeholder: 'Enter Atlassian API token',
      required: true,
      helpText: 'Generate at id.atlassian.com/manage-profile/security/api-tokens',
    },
  ],
  email_imap: [
    {
      name: 'server_url',
      label: 'IMAP Server',
      type: 'text',
      placeholder: 'imap.example.com',
      required: true,
    },
    {
      name: 'username',
      label: 'Email Address',
      type: 'email',
      placeholder: 'your-email@example.com',
      required: true,
    },
    {
      name: 'password',
      label: 'Password / App Password',
      type: 'password',
      placeholder: 'Enter password',
      required: true,
      helpText: 'Use an app-specific password if 2FA is enabled',
    },
  ],
}

export default function CloudSourceConnectPage() {
  const navigate = useNavigate()
  const { providerType } = useParams<{ providerType: string }>()
  const { user, isLoading: authLoading } = useAuth()

  const [provider, setProvider] = useState<CloudProvider | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [testResult, setTestResult] = useState<{
    success: boolean
    message: string
  } | null>(null)

  // Form state
  const [displayName, setDisplayName] = useState('')
  const [formData, setFormData] = useState<Record<string, string>>({})
  const [showPasswords, setShowPasswords] = useState<Record<string, boolean>>({})

  // Load provider details
  useEffect(() => {
    async function loadProvider() {
      if (!providerType) return
      setIsLoading(true)
      try {
        const data = await cloudSourcesApi.getProvider(providerType as CloudProviderType)
        setProvider(data)
        
        // Initialize form data
        const fields = PROVIDER_FIELDS[providerType as CloudProviderType] || []
        const initialData: Record<string, string> = {}
        fields.forEach((field) => {
          initialData[field.name] = ''
        })
        setFormData(initialData)
      } catch (err: any) {
        setError(err.message || 'Failed to load provider details')
      } finally {
        setIsLoading(false)
      }
    }

    if (!authLoading && user) {
      loadProvider()
    }
  }, [providerType, authLoading, user])

  const handleInputChange = (name: string, value: string) => {
    setFormData((prev) => ({ ...prev, [name]: value }))
  }

  const togglePasswordVisibility = (name: string) => {
    setShowPasswords((prev) => ({ ...prev, [name]: !prev[name] }))
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!provider || !displayName.trim()) return

    setIsSubmitting(true)
    setError(null)
    setTestResult(null)

    try {
      const connection = await cloudSourcesApi.createConnection({
        provider: provider.provider_type,
        display_name: displayName.trim(),
        ...formData,
      })

      // Test the connection
      const testRes = await cloudSourcesApi.testConnection(connection.id)
      if (testRes.success) {
        navigate(`/cloud-sources/connections/${connection.id}`)
      } else {
        setTestResult(testRes)
      }
    } catch (err: any) {
      setError(err.message || 'Failed to create connection')
    } finally {
      setIsSubmitting(false)
    }
  }

  if (authLoading || isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <ArrowPathIcon className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  if (!user) {
    navigate('/login')
    return null
  }

  if (!provider) {
    return (
      <div className="text-center py-12">
        <CloudIcon className="h-16 w-16 text-gray-300 dark:text-gray-600 mx-auto mb-4" />
        <h2 className="text-xl font-semibold text-primary-900 dark:text-gray-100 mb-2">
          Provider Not Found
        </h2>
        <p className="text-secondary dark:text-gray-400 mb-6">
          The requested provider could not be found.
        </p>
        <button
          onClick={() => navigate('/cloud-sources')}
          className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-primary text-white hover:bg-primary-700"
        >
          Back to Cloud Sources
        </button>
      </div>
    )
  }

  const fields = PROVIDER_FIELDS[provider.provider_type] || []

  return (
    <div className="max-w-2xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4 mb-6">
        <button
          onClick={() => navigate('/cloud-sources')}
          className="p-2 rounded-xl hover:bg-gray-100 dark:hover:bg-gray-700"
        >
          <ArrowLeftIcon className="h-5 w-5 text-primary-900 dark:text-gray-200" />
        </button>
        <div className="flex items-center gap-3">
          <span className="text-3xl">{PROVIDER_ICONS[provider.provider_type]}</span>
          <div>
            <h1 className="text-2xl font-bold text-primary-900 dark:text-gray-100">
              Connect {provider.display_name}
            </h1>
            <p className="text-secondary dark:text-gray-400">{provider.description}</p>
          </div>
        </div>
      </div>

      {/* Connection Form */}
      <div className="rounded-2xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Errors */}
          {error && (
            <div className="rounded-xl bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 p-4">
              <div className="flex items-center gap-3">
                <ExclamationTriangleIcon className="h-5 w-5 text-red-500" />
                <p className="text-red-700 dark:text-red-400">{error}</p>
              </div>
            </div>
          )}

          {/* Test Result */}
          {testResult && (
            <div
              className={`rounded-xl p-4 border ${
                testResult.success
                  ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800'
                  : 'bg-amber-50 dark:bg-amber-900/20 border-amber-200 dark:border-amber-800'
              }`}
            >
              <div className="flex items-center gap-3">
                {testResult.success ? (
                  <CheckCircleIcon className="h-5 w-5 text-green-500" />
                ) : (
                  <ExclamationTriangleIcon className="h-5 w-5 text-amber-500" />
                )}
                <p
                  className={
                    testResult.success
                      ? 'text-green-700 dark:text-green-400'
                      : 'text-amber-700 dark:text-amber-400'
                  }
                >
                  {testResult.message}
                </p>
              </div>
            </div>
          )}

          {/* Display Name */}
          <div>
            <label className="block text-sm font-medium text-primary-900 dark:text-gray-100 mb-2">
              Connection Name <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={`My ${provider.display_name}`}
              required
              className="w-full px-4 py-3 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100 focus:ring-2 focus:ring-primary focus:border-transparent"
            />
            <p className="text-xs text-secondary dark:text-gray-400 mt-1">
              A friendly name to identify this connection
            </p>
          </div>

          {/* Provider-specific fields */}
          {fields.map((field) => (
            <div key={field.name}>
              <label className="block text-sm font-medium text-primary-900 dark:text-gray-100 mb-2">
                {field.label} {field.required && <span className="text-red-500">*</span>}
              </label>
              <div className="relative">
                <input
                  type={
                    field.type === 'password'
                      ? showPasswords[field.name]
                        ? 'text'
                        : 'password'
                      : field.type
                  }
                  value={formData[field.name] || ''}
                  onChange={(e) => handleInputChange(field.name, e.target.value)}
                  placeholder={field.placeholder}
                  required={field.required}
                  className="w-full px-4 py-3 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-primary-900 dark:text-gray-100 focus:ring-2 focus:ring-primary focus:border-transparent pr-12"
                />
                {field.type === 'password' && (
                  <button
                    type="button"
                    onClick={() => togglePasswordVisibility(field.name)}
                    className="absolute right-3 top-1/2 transform -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
                  >
                    {showPasswords[field.name] ? (
                      <EyeSlashIcon className="h-5 w-5" />
                    ) : (
                      <EyeIcon className="h-5 w-5" />
                    )}
                  </button>
                )}
              </div>
              {field.helpText && (
                <p className="text-xs text-secondary dark:text-gray-400 mt-1">{field.helpText}</p>
              )}
            </div>
          ))}

          {/* Setup Instructions */}
          {provider.setup_instructions && (
            <div className="rounded-xl bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 p-4">
              <h3 className="text-sm font-medium text-blue-800 dark:text-blue-300 mb-2">
                Setup Instructions
              </h3>
              <p className="text-sm text-blue-700 dark:text-blue-400 whitespace-pre-line">
                {provider.setup_instructions}
              </p>
              {provider.documentation_url && (
                <a
                  href={provider.documentation_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-block mt-2 text-sm text-blue-600 dark:text-blue-400 hover:underline"
                >
                  View Documentation →
                </a>
              )}
            </div>
          )}

          {/* Security Note */}
          <div className="rounded-xl bg-gray-50 dark:bg-gray-700/50 p-4 flex items-start gap-3">
            <LockClosedIcon className="h-5 w-5 text-gray-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm text-secondary dark:text-gray-400">
                Your credentials are encrypted and stored securely. We never share your credentials
                with third parties.
              </p>
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center justify-end gap-3 pt-4">
            <button
              type="button"
              onClick={() => navigate('/cloud-sources')}
              className="px-6 py-3 rounded-xl border border-gray-200 dark:border-gray-600 text-primary-900 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting || !displayName.trim()}
              className="px-6 py-3 rounded-xl bg-primary text-white hover:bg-primary-700 disabled:opacity-50 flex items-center gap-2"
            >
              {isSubmitting && <ArrowPathIcon className="h-5 w-5 animate-spin" />}
              {isSubmitting ? 'Connecting...' : 'Connect'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

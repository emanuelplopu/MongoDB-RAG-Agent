/**
 * EmptyState - Consistent empty state displays across the app
 * Shows helpful messages and actions when content is empty
 */

import { useTranslation } from 'react-i18next'
import {
  DocumentTextIcon,
  MagnifyingGlassIcon,
  ChatBubbleLeftRightIcon,
  FolderIcon,
  CloudIcon,
  InboxIcon,
  PlusIcon,
} from '@heroicons/react/24/outline'

interface EmptyStateProps {
  /** Main title */
  title: string
  /** Description text */
  description?: string
  /** Icon to display */
  icon?: React.ComponentType<{ className?: string }>
  /** Primary action button */
  action?: {
    label: string
    onClick: () => void
  }
  /** Secondary action */
  secondaryAction?: {
    label: string
    onClick: () => void
  }
  /** Custom icon element */
  customIcon?: React.ReactNode
  /** Size variant */
  size?: 'sm' | 'md' | 'lg'
  /** Additional class name */
  className?: string
}

export default function EmptyState({
  title,
  description,
  icon: Icon = InboxIcon,
  action,
  secondaryAction,
  customIcon,
  size = 'md',
  className = '',
}: EmptyStateProps) {
  const sizeClasses = {
    sm: {
      container: 'py-8',
      icon: 'w-12 h-12',
      iconBg: 'w-16 h-16',
      title: 'text-base',
      description: 'text-sm',
    },
    md: {
      container: 'py-12',
      icon: 'w-16 h-16',
      iconBg: 'w-20 h-20',
      title: 'text-lg',
      description: 'text-base',
    },
    lg: {
      container: 'py-16',
      icon: 'w-20 h-20',
      iconBg: 'w-24 h-24',
      title: 'text-xl',
      description: 'text-base',
    },
  }

  const sizes = sizeClasses[size]

  return (
    <div className={`flex flex-col items-center justify-center text-center ${sizes.container} ${className}`}>
      {/* Icon */}
      <div className={`${sizes.iconBg} bg-gray-100 dark:bg-gray-800 rounded-full flex items-center justify-center mb-4`}>
        {customIcon || <Icon className={`${sizes.icon} text-gray-400 dark:text-gray-500`} />}
      </div>
      
      {/* Title */}
      <h3 className={`${sizes.title} font-semibold text-gray-900 dark:text-gray-100 mb-2`}>
        {title}
      </h3>
      
      {/* Description */}
      {description && (
        <p className={`${sizes.description} text-gray-500 dark:text-gray-400 max-w-md mb-6`}>
          {description}
        </p>
      )}
      
      {/* Actions */}
      {(action || secondaryAction) && (
        <div className="flex flex-col sm:flex-row gap-3">
          {action && (
            <button
              onClick={action.onClick}
              className="inline-flex items-center justify-center gap-2 px-4 py-2 bg-primary text-white rounded-xl hover:bg-primary-700 transition-colors"
            >
              <PlusIcon className="w-4 h-4" />
              {action.label}
            </button>
          )}
          {secondaryAction && (
            <button
              onClick={secondaryAction.onClick}
              className="inline-flex items-center justify-center px-4 py-2 text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 rounded-xl hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
            >
              {secondaryAction.label}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * Preset empty states for common scenarios
 */

export function NoDocuments({ onUpload }: { onUpload?: () => void }) {
  const { t } = useTranslation()
  return (
    <EmptyState
      icon={DocumentTextIcon}
      title={t('emptyStates.noDocuments.title')}
      description={t('emptyStates.noDocuments.description')}
      action={onUpload ? { label: t('emptyStates.noDocuments.action'), onClick: onUpload } : undefined}
    />
  )
}

export function NoSearchResults({ query, onClear }: { query?: string; onClear?: () => void }) {
  const { t } = useTranslation()
  return (
    <EmptyState
      icon={MagnifyingGlassIcon}
      title={t('emptyStates.noResults.title')}
      description={query 
        ? t('emptyStates.noResults.description', { query })
        : t('emptyStates.noResults.descriptionNoQuery')
      }
      action={onClear ? { label: t('emptyStates.noResults.action'), onClick: onClear } : undefined}
    />
  )
}

export function NoChats({ onNewChat }: { onNewChat?: () => void }) {
  const { t } = useTranslation()
  return (
    <EmptyState
      icon={ChatBubbleLeftRightIcon}
      title={t('emptyStates.noChats.title')}
      description={t('emptyStates.noChats.description')}
      action={onNewChat ? { label: t('emptyStates.noChats.action'), onClick: onNewChat } : undefined}
    />
  )
}

export function NoFolders({ onCreate }: { onCreate?: () => void }) {
  const { t } = useTranslation()
  return (
    <EmptyState
      icon={FolderIcon}
      title={t('emptyStates.noFolders.title')}
      description={t('emptyStates.noFolders.description')}
      action={onCreate ? { label: t('emptyStates.noFolders.action'), onClick: onCreate } : undefined}
      size="sm"
    />
  )
}

export function NoCloudSources({ onConnect }: { onConnect?: () => void }) {
  const { t } = useTranslation()
  return (
    <EmptyState
      icon={CloudIcon}
      title={t('emptyStates.noCloudSources.title')}
      description={t('emptyStates.noCloudSources.description')}
      action={onConnect ? { label: t('emptyStates.noCloudSources.action'), onClick: onConnect } : undefined}
    />
  )
}

export function NoNotifications() {
  const { t } = useTranslation()
  return (
    <EmptyState
      icon={InboxIcon}
      title={t('emptyStates.noNotifications.title')}
      description={t('emptyStates.noNotifications.description')}
      size="sm"
    />
  )
}

/**
 * Generic loading placeholder with empty state styling
 */
export function LoadingPlaceholder({ 
  message = 'Loading...',
  className = '',
}: { 
  message?: string
  className?: string
}) {
  return (
    <div className={`flex flex-col items-center justify-center py-12 ${className}`}>
      <div className="w-16 h-16 bg-gray-100 dark:bg-gray-800 rounded-full flex items-center justify-center mb-4">
        <div className="w-8 h-8 border-2 border-primary border-t-transparent rounded-full animate-spin" />
      </div>
      <p className="text-gray-500 dark:text-gray-400">{message}</p>
    </div>
  )
}

/**
 * ConnectionStatus - Shows API connection status indicator
 * Displays a small indicator showing if the backend is reachable
 */

import { useState, useEffect, useCallback } from 'react'
import { 
  WifiIcon,
  ExclamationTriangleIcon,
  ArrowPathIcon,
} from '@heroicons/react/24/outline'

interface ConnectionStatusProps {
  /** URL to check for connectivity */
  healthCheckUrl?: string
  /** Check interval in milliseconds */
  checkInterval?: number
  /** Show detailed status text */
  showText?: boolean
  /** Size variant */
  size?: 'sm' | 'md'
  /** Position variant */
  position?: 'inline' | 'fixed'
}

type ConnectionState = 'connected' | 'disconnected' | 'checking' | 'slow'

export default function ConnectionStatus({
  healthCheckUrl = '/api/health',
  checkInterval = 30000, // 30 seconds
  showText = false,
  size = 'sm',
  position = 'inline',
}: ConnectionStatusProps) {
  const [status, setStatus] = useState<ConnectionState>('checking')
  const [latency, setLatency] = useState<number | null>(null)
  const [lastCheck, setLastCheck] = useState<Date | null>(null)

  const checkConnection = useCallback(async () => {
    setStatus('checking')
    const startTime = Date.now()
    
    try {
      const response = await fetch(healthCheckUrl, {
        method: 'GET',
        cache: 'no-cache',
      })
      
      const endTime = Date.now()
      const responseLatency = endTime - startTime
      setLatency(responseLatency)
      setLastCheck(new Date())
      
      if (response.ok) {
        // Consider > 2000ms as slow
        setStatus(responseLatency > 2000 ? 'slow' : 'connected')
      } else {
        setStatus('disconnected')
      }
    } catch {
      setStatus('disconnected')
      setLatency(null)
    }
  }, [healthCheckUrl])

  // Initial check and interval
  useEffect(() => {
    checkConnection()
    const interval = setInterval(checkConnection, checkInterval)
    return () => clearInterval(interval)
  }, [checkConnection, checkInterval])

  // Listen for online/offline events
  useEffect(() => {
    const handleOnline = () => checkConnection()
    const handleOffline = () => setStatus('disconnected')
    
    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)
    
    return () => {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [checkConnection])

  const statusConfig = {
    connected: {
      color: 'bg-green-500',
      text: 'Connected',
      icon: WifiIcon,
      textColor: 'text-green-600 dark:text-green-400',
    },
    disconnected: {
      color: 'bg-red-500',
      text: 'Disconnected',
      icon: ExclamationTriangleIcon,
      textColor: 'text-red-600 dark:text-red-400',
    },
    checking: {
      color: 'bg-yellow-500',
      text: 'Checking...',
      icon: ArrowPathIcon,
      textColor: 'text-yellow-600 dark:text-yellow-400',
    },
    slow: {
      color: 'bg-yellow-500',
      text: 'Slow connection',
      icon: WifiIcon,
      textColor: 'text-yellow-600 dark:text-yellow-400',
    },
  }

  const config = statusConfig[status]
  const StatusIcon = config.icon
  
  const sizeClasses = {
    sm: {
      dot: 'w-2 h-2',
      icon: 'w-3 h-3',
      text: 'text-xs',
      container: 'gap-1.5',
    },
    md: {
      dot: 'w-3 h-3',
      icon: 'w-4 h-4',
      text: 'text-sm',
      container: 'gap-2',
    },
  }

  const sizes = sizeClasses[size]

  const positionClasses = position === 'fixed' 
    ? 'fixed bottom-4 right-4 bg-white dark:bg-gray-800 shadow-lg rounded-full px-3 py-1.5 z-50'
    : ''

  return (
    <div 
      className={`flex items-center ${sizes.container} ${positionClasses}`}
      title={`${config.text}${latency ? ` (${latency}ms)` : ''}${lastCheck ? ` - Last checked: ${lastCheck.toLocaleTimeString()}` : ''}`}
    >
      {/* Status dot with animation */}
      <div className="relative">
        <div className={`${sizes.dot} rounded-full ${config.color} ${status === 'checking' ? 'animate-pulse' : ''}`} />
        {status === 'connected' && (
          <div className={`absolute inset-0 ${sizes.dot} rounded-full ${config.color} animate-ping opacity-75`} />
        )}
      </div>
      
      {showText && (
        <div className={`flex items-center gap-1 ${config.textColor} ${sizes.text}`}>
          <StatusIcon className={`${sizes.icon} ${status === 'checking' ? 'animate-spin' : ''}`} />
          <span>{config.text}</span>
          {latency && status !== 'checking' && (
            <span className="text-secondary dark:text-gray-500">({latency}ms)</span>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * Minimal connection indicator - just a colored dot
 */
export function ConnectionDot({ 
  className = '',
  size = 'sm',
}: { 
  className?: string
  size?: 'xs' | 'sm' | 'md'
}) {
  const [isOnline, setIsOnline] = useState(navigator.onLine)

  useEffect(() => {
    const handleOnline = () => setIsOnline(true)
    const handleOffline = () => setIsOnline(false)
    
    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)
    
    return () => {
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [])

  const sizeClasses = {
    xs: 'w-1.5 h-1.5',
    sm: 'w-2 h-2',
    md: 'w-3 h-3',
  }

  return (
    <div 
      className={`
        ${sizeClasses[size]} rounded-full 
        ${isOnline ? 'bg-green-500' : 'bg-red-500'}
        ${className}
      `}
      title={isOnline ? 'Online' : 'Offline'}
    />
  )
}

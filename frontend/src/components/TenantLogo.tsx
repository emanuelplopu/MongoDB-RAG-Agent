import { useState } from 'react'
import { useTenant } from '../contexts/TenantContext'

interface TenantLogoProps {
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const sizeMap = {
  sm: { container: 'w-8 h-8', icon: 'h-5 w-5', img: 'h-8 w-auto', text: 'text-sm' },
  md: { container: 'w-14 h-14', icon: 'h-8 w-8', img: 'h-14 w-auto', text: 'text-lg' },
  lg: { container: 'w-20 h-20', icon: 'h-12 w-12', img: 'h-20 w-auto', text: 'text-2xl' },
}

export default function TenantLogo({ size = 'md', className = '' }: TenantLogoProps) {
  const { tenant } = useTenant()
  const dims = sizeMap[size]
  const [imgFailed, setImgFailed] = useState(false)

  // Check if logo is a detailed image (PNG/JPG) vs simple SVG icon
  const isPngLogo = tenant.branding.logoUrl?.match(/\.(png|jpg|jpeg)$/i)

  const fallback = (
    <div className={`${dims.container} rounded-2xl bg-gradient-brand flex items-center justify-center shadow-xl ${className}`}>
      <span className={`text-white font-bold ${dims.text}`}>{tenant.branding.appName.charAt(0)}</span>
    </div>
  )

  if (tenant.branding.logoUrl && !imgFailed) {
    // For detailed PNG/JPG logos (e.g. Quellex), render without gradient box
    if (isPngLogo) {
      return (
        <img
          src={tenant.branding.logoUrl}
          alt={tenant.branding.appName}
          className={`${dims.img} object-contain ${className}`}
          onError={() => setImgFailed(true)}
        />
      )
    }
    // For SVG logos, use the gradient container
    return (
      <div className={`${dims.container} rounded-2xl bg-gradient-brand flex items-center justify-center shadow-xl ${className}`}>
        <img
          src={tenant.branding.logoUrl}
          alt={tenant.branding.appName}
          className={dims.icon}
          onError={() => setImgFailed(true)}
        />
      </div>
    )
  }

  return fallback
}

import { Link as RouterLink, LinkProps as RouterLinkProps } from 'react-router-dom'
import { Ref } from 'react'
import { useLocalizedPath } from '../hooks/useLocalizedNavigate'

// Localized Link component
interface LocalizedLinkProps extends Omit<RouterLinkProps, 'to'> {
  to: string
  /** React 19: `ref` is an ordinary prop, no forwardRef needed. */
  ref?: Ref<HTMLAnchorElement>
}

export function LocalizedLink({ to, children, ref, ...props }: LocalizedLinkProps) {
  const getLocalizedPath = useLocalizedPath()

  return (
    <RouterLink ref={ref} to={getLocalizedPath(to)} {...props}>
      {children}
    </RouterLink>
  )
}

// Color scale type for primary/secondary colors
export interface ColorScale {
  DEFAULT: string
  50: string
  100: string
  200: string
  300: string
  400: string
  500: string
  600: string
  700: string
  800: string
  900: string
}

export interface TenantConfig {
  id: string
  branding: TenantBranding
  theme: TenantTheme
  features: TenantFeatures
  content: TenantContent
}

export interface TenantBranding {
  appName: string
  tagline: string
  logoUrl?: string           // Path to logo SVG/PNG in public/tenants/{id}/
  iconUrl?: string           // Path to small icon (used in collapsed sidebar, etc.)
  faviconUrl?: string        // Path to favicon
}

export interface TenantTheme {
  colors: {
    primary: ColorScale
    secondary: ColorScale
    accent?: Partial<ColorScale>
    surface: { DEFAULT: string; variant: string }
    background: { DEFAULT: string; dark: string }
    gradient: { from: string; to: string }
  }
  fonts: {
    sans: string[]
    display?: string[]
    mono?: string[]
  }
  borderRadius: {
    base: string
    button: string
    input: string
  }
  darkMode: {
    background: string
    surface: string
    text: string
  }
}

export interface TenantFeatures {
  showCloudSources: boolean
  showEmailConfig: boolean
  showProfiles: boolean
  showStrategies: boolean
  showEmbeddingBenchmark: boolean
  showBackups: boolean
  showApiDocs: boolean
  landingPageVariant: 'default' | 'minimal' | 'professional' | 'quellex'
}

export interface TenantContent {
  i18nOverrides: Record<string, Record<string, string>>
  // key is language code ("en", "de"), value is flat key-value map of i18n overrides
}

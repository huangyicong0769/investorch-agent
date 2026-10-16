import { createContext, useContext, useLayoutEffect, useState, type PropsWithChildren, type Dispatch, type SetStateAction } from 'react'

import type { ImageConfig, WebConfig } from '../api/types'

type ThemePreview = Pick<WebConfig, 'theme_color' | 'theme_mode'>
const WebConfigContext = createContext<{
  config: WebConfig
  previewTheme: Dispatch<SetStateAction<ThemePreview | null>>
} | null>(null)

export function WebConfigProvider({ children, value }: PropsWithChildren<{ value: WebConfig }>) {
  const [preview, previewTheme] = useState<ThemePreview | null>(null)
  const color = preview?.theme_color ?? value.theme_color
  const mode = preview?.theme_mode ?? value.theme_mode
  useLayoutEffect(() => {
    document.documentElement.dataset.themeColor = color
    return () => { delete document.documentElement.dataset.themeColor }
  }, [color])
  useLayoutEffect(() => {
    const system = window.matchMedia('(prefers-color-scheme: dark)')
    const apply = () => {
      document.documentElement.dataset.colorScheme = mode === 'system' ? (system.matches ? 'dark' : 'light') : mode
    }
    apply()
    if (mode === 'system') system.addEventListener('change', apply)
    return () => {
      system.removeEventListener('change', apply)
      delete document.documentElement.dataset.colorScheme
    }
  }, [mode])
  return <WebConfigContext.Provider value={{ config: value, previewTheme }}>{children}</WebConfigContext.Provider>
}

export function useWebConfig(): WebConfig {
  const config = useContext(WebConfigContext)
  if (config === null) {
    throw new Error('WebConfigProvider is missing.')
  }
  return config.config
}

export function useThemePreview() {
  const context = useContext(WebConfigContext)
  if (!context) throw new Error('WebConfigProvider is missing.')
  return context.previewTheme
}

const ImageConfigContext = createContext<ImageConfig | null>(null)

export function ImageConfigProvider({ children, value }: PropsWithChildren<{ value: ImageConfig }>) {
  return <ImageConfigContext.Provider value={value}>{children}</ImageConfigContext.Provider>
}

export function useImageConfig(): ImageConfig {
  const config = useContext(ImageConfigContext)
  if (config === null) throw new Error('ImageConfigProvider is missing.')
  return config
}

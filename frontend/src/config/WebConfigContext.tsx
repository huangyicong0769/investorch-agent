import { createContext, useContext, useLayoutEffect, useState, type PropsWithChildren, type Dispatch, type SetStateAction } from 'react'

import type { ImageConfig, WebConfig } from '../api/types'

type ThemeColor = WebConfig['theme_color']
const WebConfigContext = createContext<{
  config: WebConfig
  previewTheme: Dispatch<SetStateAction<ThemeColor | null>>
} | null>(null)

export function WebConfigProvider({ children, value }: PropsWithChildren<{ value: WebConfig }>) {
  const [preview, previewTheme] = useState<ThemeColor | null>(null)
  const color = preview ?? value.theme_color
  useLayoutEffect(() => {
    document.documentElement.dataset.themeColor = color
    return () => { delete document.documentElement.dataset.themeColor }
  }, [color])
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

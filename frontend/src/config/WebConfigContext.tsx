import { createContext, useContext, type PropsWithChildren } from 'react'

import type { ImageConfig, WebConfig } from '../api/types'

const WebConfigContext = createContext<WebConfig | null>(null)

export function WebConfigProvider({ children, value }: PropsWithChildren<{ value: WebConfig }>) {
  return <WebConfigContext.Provider value={value}>{children}</WebConfigContext.Provider>
}

export function useWebConfig(): WebConfig {
  const config = useContext(WebConfigContext)
  if (config === null) {
    throw new Error('WebConfigProvider is missing.')
  }
  return config
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

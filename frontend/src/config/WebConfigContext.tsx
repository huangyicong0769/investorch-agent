import { createContext, useContext, type PropsWithChildren } from 'react'

import type { WebConfig } from '../api/types'

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

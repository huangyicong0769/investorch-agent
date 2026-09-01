import { MutationCache, QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError } from '../api/client'
import { bootstrapQueryOptions } from '../api/queries'
import { WebConfigProvider } from '../config/WebConfigContext'
import { errorMessage } from '../lib/errors'
import { Toaster } from '@/components/ui/sonner'
import { AppRouter } from './router'

const mutationCache = new MutationCache({
  onError: (error) => {
    if (
      error instanceof ApiError &&
      error.code === 'compaction_failed' &&
      error.details.consistency_uncertain === true
    ) {
      toast.error('Context storage may be damaged. See system log.', { duration: Infinity })
      return
    }
    toast.error(errorMessage(error, 'The action could not be completed. Try again.'))
  },
})

export const queryClient = new QueryClient({
  mutationCache,
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
    },
  },
})

function ConfiguredApp() {
  const bootstrapQuery = useQuery(bootstrapQueryOptions())

  if (bootstrapQuery.isPending) {
    return <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">Loading…</div>
  }
  if (bootstrapQuery.isError) {
    return <div className="flex min-h-screen items-center justify-center text-sm text-destructive">Unable to load configuration.</div>
  }

  return (
    <WebConfigProvider value={bootstrapQuery.data.web_config}>
      <AppRouter />
      <Toaster position="bottom-right" />
    </WebConfigProvider>
  )
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfiguredApp />
    </QueryClientProvider>
  )
}

export default App

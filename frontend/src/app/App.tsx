import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError } from '../api/client'
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

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppRouter />
      <Toaster position="bottom-right" />
    </QueryClientProvider>
  )
}

export default App

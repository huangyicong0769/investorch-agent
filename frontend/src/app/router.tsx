import { BrowserRouter, Outlet, Route, Routes, useParams } from 'react-router-dom'

function AppShell() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <main className="mx-auto flex min-h-screen w-full max-w-5xl flex-col px-6 py-8">
        <Outlet />
      </main>
    </div>
  )
}

function HomeRoute() {
  return (
    <section className="flex flex-1 items-center justify-center">
      <div className="text-center">
        <p className="text-sm font-medium text-muted-foreground">QMT Agent</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight">Ask QMT Agent anything.</h1>
      </div>
    </section>
  )
}

function SessionRoute() {
  const { sessionId } = useParams<'sessionId'>()

  return (
    <section className="flex flex-1 items-center justify-center">
      <div className="text-center">
        <p className="text-sm font-medium text-muted-foreground">QMT Agent session</p>
        <h1 className="mt-3 text-2xl font-semibold tracking-tight">{sessionId}</h1>
      </div>
    </section>
  )
}

function NotFoundRoute() {
  return (
    <section className="flex flex-1 items-center justify-center">
      <p className="text-sm text-muted-foreground">This page does not exist.</p>
    </section>
  )
}

export function AppRouter() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<HomeRoute />} />
          <Route path="c/:sessionId" element={<SessionRoute />} />
          <Route path="*" element={<NotFoundRoute />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export type WebSocketConnectionStatus = 'connected' | 'reconnecting' | 'disconnected'

export interface WebSocketConnectionOptions {
  onMessage: (payload: unknown) => void
  onStatusChange: (status: WebSocketConnectionStatus) => void
  baseDelayMs?: number
  maxDelayMs?: number
}

export interface WebSocketConnection {
  close: () => void
}

const DEFAULT_BASE_DELAY_MS = 500
const DEFAULT_MAX_DELAY_MS = 8_000

/** Build the same-origin websocket endpoint used by the browser application. */
export function websocketUrl(): string {
  if (typeof window === 'undefined') {
    return '/ws'
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws`
}

/**
 * Own one receive-only websocket and reconnect it with a bounded backoff.
 *
 * The handle intentionally exposes no send method: the browser stream is a
 * server-to-client notification channel, while mutations continue to use the
 * HTTP API.
 */
export function createWebSocketConnection(options: WebSocketConnectionOptions): WebSocketConnection {
  const baseDelayMs = Math.max(0, options.baseDelayMs ?? DEFAULT_BASE_DELAY_MS)
  const maxDelayMs = Math.max(baseDelayMs, options.maxDelayMs ?? DEFAULT_MAX_DELAY_MS)

  let disposed = false
  let socket: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let reconnectAttempt = 0

  const clearReconnectTimer = () => {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  const setStatus = (status: WebSocketConnectionStatus) => {
    if (!disposed) {
      options.onStatusChange(status)
    }
  }

  const connect = () => {
    if (disposed || socket !== null) {
      return
    }

    setStatus('reconnecting')

    let nextSocket: WebSocket
    try {
      nextSocket = new WebSocket(websocketUrl())
    } catch {
      scheduleReconnect()
      return
    }

    socket = nextSocket

    nextSocket.onopen = () => {
      if (disposed || socket !== nextSocket) {
        return
      }
      reconnectAttempt = 0
      setStatus('connected')
    }

    nextSocket.onmessage = (event) => {
      if (disposed || socket !== nextSocket || typeof event.data !== 'string') {
        return
      }

      try {
        options.onMessage(JSON.parse(event.data) as unknown)
      } catch {
        // Malformed frames are ignored at the transport boundary.
      }
    }

    nextSocket.onerror = () => {
      // The close event owns reconnect scheduling. Some browsers emit both
      // error and close, so scheduling here would create duplicate sockets.
    }

    nextSocket.onclose = () => {
      if (disposed || socket !== nextSocket) {
        return
      }

      socket = null
      scheduleReconnect()
    }
  }

  function scheduleReconnect() {
    if (disposed || reconnectTimer !== null) {
      return
    }

    const delay = Math.min(maxDelayMs, baseDelayMs * 2 ** reconnectAttempt)
    reconnectAttempt = Math.min(reconnectAttempt + 1, 30)
    setStatus(typeof navigator !== 'undefined' && !navigator.onLine ? 'disconnected' : 'reconnecting')
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
  }

  connect()

  return {
    close: () => {
      if (disposed) {
        return
      }

      disposed = true
      clearReconnectTimer()
      const activeSocket = socket
      socket = null
      if (activeSocket) {
        activeSocket.onopen = null
        activeSocket.onmessage = null
        activeSocket.onerror = null
        activeSocket.onclose = null
        activeSocket.close()
      }
    },
  }
}

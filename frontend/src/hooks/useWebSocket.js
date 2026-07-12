import { useEffect, useRef, useCallback, useState } from 'react'

export function useWebSocket(url) {
  const wsRef = useRef(null)
  const [connected, setConnected] = useState(false)
  const [lastMessage, setLastMessage] = useState(null)
  const reconnectTimer = useRef(null)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(url)

    ws.onopen = () => {
      setConnected(true)
      console.log('WebSocket connected')
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        setLastMessage(data)
      } catch (e) {
        console.error('WS parse error:', e)
      }
    }

    ws.onclose = () => {
      setConnected(false)
      // The close callback runs only after connect has finished initialization.
      // eslint-disable-next-line react-hooks/immutability
      reconnectTimer.current = setTimeout(connect, 1000)
    }

    ws.onerror = (e) => {
      console.error('WebSocket error:', e)
    }

    wsRef.current = ws
  }, [url])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  const send = useCallback((data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data))
    }
  }, [])

  return { connected, lastMessage, send }
}

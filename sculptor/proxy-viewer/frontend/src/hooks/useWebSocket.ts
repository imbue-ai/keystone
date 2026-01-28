import { useEffect, useRef, useState, useCallback } from 'react';
import {ProxyEvent, ConnectionStatus, WebSocketMessage} from '../types';

export function useWebSocket(url: string) {
  const [events, setEvents] = useState<ProxyEvent[]>([]);
  const [status, setStatus] = useState<ConnectionStatus>('connecting');
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const connect = useCallback(() => {
    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setStatus('connected');
        ws.send('ping'); // Send initial ping
      };

      ws.onmessage = (event) => {
        try {
          const data: WebSocketMessage = JSON.parse(event.data);

          // Handle special message types
          if (data.type === 'connected' || data.type === 'pong') {
            return;
          }


          // Handle API events (including stream_complete which has full data)
          if (data.id && data.request && data.response) {
            const newEvent = data as ProxyEvent;
            // Just add the event - backend already filtered duplicates
            setEvents((prev) => [newEvent, ...prev]);
          }
        } catch (error) {
          console.error('Error processing WebSocket message:', error, event.data);
        }
      };

      ws.onclose = () => {
        setStatus('disconnected');
        wsRef.current = null;

        // Reconnect after 3 seconds
        reconnectTimeoutRef.current = setTimeout(() => {
          setStatus('connecting');
          connect();
        }, 3000);
      };

      ws.onerror = () => {
        setStatus('error');
      };
    } catch (error) {
      setStatus('error');
    }
  }, [url]);

  useEffect(() => {
    if (!url) {
      return;
    }

    connect();

    // Send periodic pings
    const pingInterval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send('ping');
      }
    }, 30000);

    return () => {
      clearInterval(pingInterval);
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [url, connect]);

  return { events, status };
}

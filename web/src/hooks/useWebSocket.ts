import { useEffect, useRef, useCallback } from "react";
import { useAuthStore } from "@/stores/authStore";

export function useWebSocket(url: string | null, onMessage: (data: unknown) => void) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef(0);

  const connect = useCallback(() => {
    if (!url) return;
    const token = useAuthStore.getState().token;
    const wsUrl = `${url}?token=${token}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      try {
        onMessage(JSON.parse(event.data));
      } catch {
        onMessage(event.data);
      }
    };

    ws.onclose = () => {
      if (reconnectRef.current < 5) {
        reconnectRef.current += 1;
        setTimeout(connect, 2000 * reconnectRef.current);
      }
    };
  }, [url, onMessage]);

  useEffect(() => {
    connect();
    return () => {
      reconnectRef.current = 99;
      wsRef.current?.close();
    };
  }, [connect]);

  return wsRef;
}

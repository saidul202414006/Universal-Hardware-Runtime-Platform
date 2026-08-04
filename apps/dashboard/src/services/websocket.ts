import { useEffect } from 'react';
import { useRuntimeStore } from '@/store/runtime';

let socket: WebSocket | null = null;
let reconnectTimer: NodeJS.Timeout | null = null;

export function useWebSocket() {
  const { setConnected, updateDevice, updateTask, removeDevice } = useRuntimeStore();

  useEffect(() => {
    // We expect the dashboard to be served from the same host, or we use localhost for dev
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname === 'localhost' ? 'localhost:8765' : window.location.host;

    // In a real app, API key should be injected securely. For development we assume no key or dev key.
    // The backend uses 'test-api-key-12345' in our tests, but in production it's auto-generated.
    // For now we'll connect without one assuming public health endpoint is enough, or pass dev key.
    const wsUrl = `${protocol}//${host}/ws/v1/events?api_key=dev`;

    function connect() {
      if (socket?.readyState === WebSocket.OPEN) return;

      console.log('Connecting to Runtime WebSocket...');
      socket = new WebSocket(wsUrl);

      socket.onopen = () => {
        console.log('WebSocket connected');
        setConnected(true);
      };

      socket.onclose = () => {
        console.log('WebSocket disconnected');
        setConnected(false);
        socket = null;
        reconnectTimer = setTimeout(connect, 3000);
      };

      socket.onerror = (err) => {
        console.error('WebSocket error:', err);
      };

      socket.onmessage = (msg) => {
        try {
          const data = JSON.parse(msg.data);
          if (data.type === 'event') {
            handleEvent(data);
          }
        } catch (err) {
          console.error('Failed to parse WebSocket message:', err);
        }
      };
    }

    function handleEvent(event: any) {
      const { event_type, payload } = event;

      if (event_type === 'device.connected' || event_type === 'device.state_changed') {
        updateDevice(payload);
      } else if (event_type === 'device.disconnected') {
        removeDevice(payload.device_id);
      } else if (event_type.startsWith('task.')) {
        updateTask(payload);
      }
    }

    connect();

    return () => {
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (socket) {
        socket.close();
        socket = null;
      }
    };
  }, [setConnected, updateDevice, updateTask, removeDevice]);
}

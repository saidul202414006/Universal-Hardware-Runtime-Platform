import { create } from 'zustand';

export interface Device {
  id: string;
  name: string;
  type: string;
  port: string;
  state: 'detected' | 'identifying' | 'ready' | 'busy' | 'offline' | 'error';
  capabilities: string[];
  vid: string | null;
  pid: string | null;
}

export interface Task {
  id: string;
  task_type: string;
  state: string;
  progress: number;
  message: string;
  device_id: string | null;
  log_lines?: string[];
}

export interface SystemHealth {
  overall: 'healthy' | 'degraded' | 'unhealthy' | 'unknown';
  runtime_version: string;
  uptime_seconds: number;
}

interface RuntimeState {
  devices: Record<string, Device>;
  tasks: Record<string, Task>;
  health: SystemHealth | null;
  connected: boolean;

  // Actions
  setDevices: (devices: Device[]) => void;
  updateDevice: (device: Device) => void;
  removeDevice: (deviceId: string) => void;

  setTasks: (tasks: Task[]) => void;
  updateTask: (task: Task) => void;

  setHealth: (health: SystemHealth) => void;
  setConnected: (status: boolean) => void;
}

export const useRuntimeStore = create<RuntimeState>((set) => ({
  devices: {},
  tasks: {},
  health: null,
  connected: false,

  setDevices: (devices) => set((state) => {
    const next = { ...state.devices };
    devices.forEach(d => { next[d.id] = d; });
    return { devices: next };
  }),

  updateDevice: (device) => set((state) => ({
    devices: { ...state.devices, [device.id]: device }
  })),

  removeDevice: (deviceId) => set((state) => {
    const next = { ...state.devices };
    delete next[deviceId];
    return { devices: next };
  }),

  setTasks: (tasks) => set((state) => {
    const next = { ...state.tasks };
    tasks.forEach(t => { next[t.id] = t; });
    return { tasks: next };
  }),

  updateTask: (task) => set((state) => ({
    tasks: { ...state.tasks, [task.id]: { ...state.tasks[task.id], ...task } }
  })),

  setHealth: (health) => set({ health }),
  setConnected: (status) => set({ connected: status }),
}));

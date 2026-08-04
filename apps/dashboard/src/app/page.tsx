"use client";

import { useRuntimeStore } from "@/store/runtime";
import { Activity, Cpu, Server, TerminalSquare } from "lucide-react";
import { useEffect, useState } from "react";

export default function Home() {
  const { devices, tasks, health, connected } = useRuntimeStore();
  const deviceList = Object.values(devices);
  const taskList = Object.values(tasks);

  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  if (!mounted) return null;

  return (
    <div className="space-y-6 text-zinc-300">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-white font-mono">Overview</h1>
          <p className="text-zinc-500 mt-1">Universal Hardware Runtime Dashboard</p>
        </div>
      </div>

      <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-4">
        {/* Connection Status */}
        <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-6 relative overflow-hidden group">
          <div className="absolute top-0 right-0 p-4 opacity-10 transition-opacity group-hover:opacity-20">
            <Server className="w-16 h-16 text-green-500" />
          </div>
          <h3 className="text-sm font-medium text-zinc-400">Runtime Status</h3>
          <div className="mt-2 flex items-center gap-3">
            <span className="relative flex h-3 w-3">
              {connected ? (
                <>
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
                  <span className="relative inline-flex rounded-full h-3 w-3 bg-green-500"></span>
                </>
              ) : (
                <span className="relative inline-flex rounded-full h-3 w-3 bg-red-500"></span>
              )}
            </span>
            <span className="text-2xl font-semibold font-mono text-white">
              {connected ? "ONLINE" : "OFFLINE"}
            </span>
          </div>
        </div>

        {/* Devices */}
        <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-6 relative overflow-hidden group">
          <div className="absolute top-0 right-0 p-4 opacity-10 transition-opacity group-hover:opacity-20">
            <Cpu className="w-16 h-16 text-blue-500" />
          </div>
          <h3 className="text-sm font-medium text-zinc-400">Connected Devices</h3>
          <div className="mt-2 text-3xl font-semibold font-mono text-white">
            {deviceList.length}
          </div>
        </div>

        {/* Tasks */}
        <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-6 relative overflow-hidden group">
          <div className="absolute top-0 right-0 p-4 opacity-10 transition-opacity group-hover:opacity-20">
            <Activity className="w-16 h-16 text-purple-500" />
          </div>
          <h3 className="text-sm font-medium text-zinc-400">Active Tasks</h3>
          <div className="mt-2 text-3xl font-semibold font-mono text-white">
            {taskList.filter(t => t.state === 'running' || t.state === 'queued').length}
          </div>
        </div>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {/* Device List */}
        <div className="rounded-lg border border-zinc-800 bg-zinc-950 p-6">
          <h3 className="text-lg font-medium text-white mb-4 flex items-center gap-2 font-mono">
            <TerminalSquare className="w-5 h-5 text-zinc-400" />
            Active Devices
          </h3>
          {deviceList.length === 0 ? (
            <div className="text-sm text-zinc-500 border border-dashed border-zinc-800 rounded p-8 text-center font-mono">
              NO DEVICES DETECTED
            </div>
          ) : (
            <div className="space-y-3">
              {deviceList.map(device => (
                <div key={device.id} className="flex items-center justify-between p-3 rounded bg-zinc-900/50 border border-zinc-800/50 hover:border-zinc-700 transition-colors">
                  <div>
                    <p className="text-sm font-medium text-white font-mono">{device.name}</p>
                    <p className="text-xs text-zinc-500 font-mono mt-0.5">{device.port} • {device.type}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`px-2 py-1 text-[10px] uppercase font-bold rounded font-mono
                      ${device.state === 'ready' ? 'bg-green-500/10 text-green-400 border border-green-500/20' :
                        device.state === 'busy' ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20' :
                        'bg-zinc-800 text-zinc-400'}`}>
                      {device.state}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

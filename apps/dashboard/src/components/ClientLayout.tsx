"use client";

import { useWebSocket } from "@/services/websocket";
import { Sidebar } from "@/components/Sidebar";

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  useWebSocket();

  return (
    <div className="flex h-screen overflow-hidden bg-[radial-gradient(ellipse_at_top_right,_var(--tw-gradient-stops))] from-zinc-900/20 via-black to-black">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-7xl p-8">
          {children}
        </div>
      </main>
    </div>
  );
}

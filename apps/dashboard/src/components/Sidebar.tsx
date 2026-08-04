"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Cpu, LayoutDashboard, Settings, Terminal, Activity, Puzzle } from "lucide-react";
import { cn } from "@/lib/utils";

const navigation = [
  { name: "Dashboard", href: "/", icon: LayoutDashboard },
  { name: "Devices", href: "/devices", icon: Cpu },
  { name: "Serial Monitor", href: "/serial", icon: Terminal },
  { name: "Tasks", href: "/tasks", icon: Activity },
  { name: "Plugins", href: "/plugins", icon: Puzzle },
  { name: "Settings", href: "/settings", icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <div className="flex h-full w-64 flex-col bg-zinc-950 border-r border-green-900/30 font-mono text-zinc-400">
      <div className="flex h-16 items-center border-b border-green-900/30 px-6">
        <Cpu className="mr-3 h-6 w-6 text-green-500" />
        <span className="text-lg font-bold tracking-tighter text-green-500">UHR<span className="text-zinc-600">.</span>CORE</span>
      </div>

      <nav className="flex-1 space-y-1 px-3 py-4">
        {navigation.map((item) => {
          const isActive = pathname === item.href;
          return (
            <Link
              key={item.name}
              href={item.href}
              className={cn(
                "group flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-green-900/20 text-green-400"
                  : "text-zinc-400 hover:bg-zinc-900 hover:text-green-300"
              )}
            >
              <item.icon
                className={cn(
                  "mr-3 h-5 w-5 flex-shrink-0 transition-colors",
                  isActive ? "text-green-400" : "text-zinc-500 group-hover:text-green-400"
                )}
              />
              {item.name}
              {isActive && <span className="ml-auto w-1.5 h-1.5 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,1)]" />}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-green-900/30 p-4 text-xs">
        <div className="flex items-center justify-between text-zinc-500">
          <span>SYSTEM STATUS</span>
          <span className="flex items-center gap-1.5 text-green-500">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
            </span>
            ONLINE
          </span>
        </div>
      </div>
    </div>
  );
}

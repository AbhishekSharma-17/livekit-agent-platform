"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/console", label: "Agents", match: (p: string) => p === "/console" || p.startsWith("/console/agents") },
  { href: "/console/knowledge", label: "Knowledge", match: (p: string) => p.startsWith("/console/knowledge") },
  { href: "/console/sessions", label: "Sessions", match: (p: string) => p.startsWith("/console/sessions") },
];

export function ConsoleNav() {
  const pathname = usePathname() ?? "/console";

  return (
    <header className="sticky top-0 z-20 border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
        <Link href="/console" className="text-sm font-semibold tracking-tight">
          LKAP <span className="font-normal text-muted-foreground">console</span>
        </Link>
        <nav className="flex items-center gap-1">
          {LINKS.map((link) => {
            const active = link.match(pathname);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
                  active ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}

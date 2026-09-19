import { Toaster } from "@/components/ui/sonner";
import { ConsoleNav } from "@/components/console/nav";
import { ConsoleQueryProvider } from "@/components/console/lib/query-provider";
import { ThemeProvider } from "@/components/console/shell/theme-provider";

/**
 * Calm light "studio" theme for the admin console (docs/ARCHITECTURE.md D14,
 * docs/IMPLEMENTATION_PLAN.md W1-WEB-CONSOLE).
 */
export default function ConsoleLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <ThemeProvider>
      <div className="min-h-screen bg-background text-foreground">
        <ConsoleQueryProvider>
          <ConsoleNav />
          <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
          <Toaster position="bottom-right" />
        </ConsoleQueryProvider>
      </div>
    </ThemeProvider>
  );
}

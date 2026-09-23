import Link from "next/link";

import { FileQuestionIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/shared/empty-state";

/**
 * Console 404 (docs/UI_UX_SPEC.md §5.7, §7.12): "inside the shell, 'That
 * page doesn't exist' + links to Overview/Agents/Sessions." Renders as a
 * plain child of `app/console/layout.tsx`, so it already sits inside
 * whatever console shell that layout currently mounts — nothing here needs
 * to duplicate navigation.
 */
export default function ConsoleNotFound() {
  return (
    <EmptyState
      icon={FileQuestionIcon}
      title="That page doesn't exist"
      description="It may have moved, or the link was mistyped."
      action={
        <Button asChild size="sm">
          <Link href="/console">Overview</Link>
        </Button>
      }
      secondary={
        <>
          <Button asChild variant="outline" size="sm">
            <Link href="/console/agents">Agents</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link href="/console/sessions">Sessions</Link>
          </Button>
        </>
      }
    />
  );
}

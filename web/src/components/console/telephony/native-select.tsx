import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * A native `<select>` styled like `Input`: telephony pickers are short lists
 * and a native control keeps them keyboard- and screen-reader-correct
 * everywhere (including jsdom tests) with no popover.
 */
export function NativeSelect({ className, ...props }: React.ComponentProps<"select">) {
  return (
    <select
      data-slot="native-select"
      className={cn(
        "h-8 w-full min-w-0 rounded-sm border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30",
        className,
      )}
      {...props}
    />
  );
}

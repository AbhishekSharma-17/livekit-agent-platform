import type { EditorExtension } from "@/components/console/agents/editor/types";

import { CallNumberDialogHost, CallNumberMenuItem } from "./call-number";

/**
 * V2-17's agent-editor plug-in: "Call a number…" in the Test call menu. The
 * dialog is mounted through `headerActions` (always rendered, invisible until
 * opened) because the menu item unmounts with the dropdown.
 */
export const telephonyExtension: EditorExtension = {
  id: "V2-17",
  slots: {
    testCallItems: [CallNumberMenuItem],
    headerActions: [CallNumberDialogHost],
  },
};

import type { EditorExtension } from "@/components/console/agents/editor/types";

import { AddToWebsiteButton, AddToWebsiteDialogHost } from "./add-to-website-dialog";
import { TestChatDialogHost, TestChatMenuItem } from "./test-chat-dialog";

/**
 * V2-18's agent-editor plug-in: "Test chat…" in the Test call menu (a text-only
 * session with rewind/edit-and-replay, CONTRACTS-V2 §3.4) and "Add to website"
 * in the header (the widget snippet dialog). Both dialogs are mounted
 * through the always-rendered `headerActions` slot — see
 * `test-chat-dialog.tsx`/`add-to-website-dialog.tsx` docstrings — because the
 * menu item that opens the dialog unmounts with the dropdown.
 */
export const testChatExtension: EditorExtension = {
  id: "V2-18",
  slots: {
    testCallItems: [TestChatMenuItem],
    headerActions: [AddToWebsiteButton, TestChatDialogHost, AddToWebsiteDialogHost],
  },
};

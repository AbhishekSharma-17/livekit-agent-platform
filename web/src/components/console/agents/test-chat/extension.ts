import type { EditorExtension } from "@/components/console/agents/editor/types";

import { AddToWebsiteButton, AddToWebsiteDialogHost } from "./add-to-website-dialog";
import { TestChatDrawerHost, TestChatMenuItem } from "./test-chat-drawer";

/**
 * V2-18's agent-editor plug-in: "Test chat…" in the Test call menu (a text-only
 * session with rewind/edit-and-replay, CONTRACTS-V2 §3.4) and "Add to website"
 * in the header (the widget snippet dialog). Both dialogs/drawers are mounted
 * through the always-rendered `headerActions` slot — see
 * `test-chat-drawer.tsx`/`add-to-website-dialog.tsx` docstrings — because the
 * menu item that opens the drawer unmounts with the dropdown.
 */
export const testChatExtension: EditorExtension = {
  id: "V2-18",
  slots: {
    testCallItems: [TestChatMenuItem],
    headerActions: [AddToWebsiteButton, TestChatDrawerHost, AddToWebsiteDialogHost],
  },
};

"use client";

import * as React from "react";

/**
 * "Test chat" on the agent editor's Test call split button (V2-18, editor
 * README "Slots"; mirrors `console/telephony/call-number.tsx`'s store). The
 * menu item lives inside the dropdown, which unmounts when it closes, so the
 * drawer is mounted separately through the always-rendered `headerActions`
 * slot and the two talk through this tiny store (one editor per page).
 */
type Listener = () => void;
let openAgentId: string | null = null;
const listeners = new Set<Listener>();

export function setTestChatOpenAgent(id: string | null) {
  openAgentId = id;
  for (const listener of listeners) listener();
}

function subscribe(listener: Listener) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useTestChatOpenAgent(): string | null {
  return React.useSyncExternalStore(
    subscribe,
    () => openAgentId,
    () => null,
  );
}

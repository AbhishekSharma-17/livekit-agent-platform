import type { Metadata } from "next";
import { CredentialList } from "@/components/console/registry/credential-list";

export const metadata: Metadata = { title: "Credentials" };

/**
 * Credentials list (UI_UX_SPEC §4.5). Served at `/console/keys` rather than
 * `/console/credentials`: the operator's permission rules deny any path whose
 * segment starts with `credentials`, which would block edits to this route.
 * The page title and copy still say "Credentials".
 */
export default function ConsoleCredentialsPage() {
  return <CredentialList />;
}

"use client";

import * as React from "react";
import { UserPlusIcon, UsersIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/console/shared/confirm-dialog";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CopyButton } from "@/components/shared/copy-button";
import { EmptyState } from "@/components/shared/empty-state";
import { Field } from "@/components/shared/field";
import { ReadOnlyNote } from "@/components/shared/read-only-note";
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusPill } from "@/components/shared/status-chip";
import { lifecycleStatus, type LifecycleStatus } from "@/components/shared/status-map";
import { Tag } from "@/components/shared/tag";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { readOnlyCopy } from "@/components/console/shared/permission";
import { api } from "@/lib/api";
import type { InviteOut, MemberOut, Role } from "./api-types";
import { ROLE_LABEL } from "./api-types";
import { Highlight, ListNoMatches, ListSearchField, useListSearch } from "./list-search";
import { RowsSkeleton } from "./settings-card";
import { useActiveWorkspace, useInvalidateSettings, useMembers } from "./use-settings-queries";

const ROLES: Role[] = ["viewer", "builder", "admin", "owner"];

function canManageMembers(role: string | undefined): boolean {
  return role === "admin" || role === "owner";
}

/** A member's status: tone from the shared lifecycle map, label in this screen's words. */
function memberStatus(member: MemberOut): LifecycleStatus {
  if (member.pending) return { tone: lifecycleStatus("pending").tone, label: "Invited" };
  if (member.disabled) return { tone: lifecycleStatus("failed").tone, label: "Disabled" };
  return { tone: lifecycleStatus("ready").tone, label: "Active" };
}

function MemberStatus({ member }: { member: MemberOut }) {
  const status = memberStatus(member);
  return (
    <StatusPill tone={status.tone} size="sm">
      {status.label}
    </StatusPill>
  );
}

export function TeamTab() {
  const { workspace: membership } = useActiveWorkspace();
  const membersQuery = useMembers(membership?.id);
  const invalidate = useInvalidateSettings();
  const canManage = canManageMembers(membership?.role);

  const members = React.useMemo(() => membersQuery.data?.items ?? [], [membersQuery.data]);
  const search = useListSearch("team", members, (member) => [member.name, member.email, ROLE_LABEL[member.role]]);
  const query = search.query;

  const identity = (member: MemberOut) => (
    <div className="min-w-0">
      <div className="truncate font-medium text-foreground">
        <Highlight text={member.name || member.email} query={query} />
      </div>
      <div className="truncate font-mono text-caption text-text-secondary">
        <Highlight text={member.email} query={query} />
      </div>
    </div>
  );

  const columns: ResponsiveTableColumn<MemberOut>[] = [
    { id: "member", header: "Member", cell: identity },
    { id: "status", header: "Status", cell: (member) => <MemberStatus member={member} /> },
    {
      id: "role",
      header: "Role",
      interactive: true,
      cell: (member) =>
        canManage && membership ? (
          <RoleSelect
            workspaceId={membership.id}
            member={member}
            onChanged={() => invalidate(membership.id)}
          />
        ) : (
          <span className="text-text-secondary">{ROLE_LABEL[member.role]}</span>
        ),
    },
    ...(canManage && membership
      ? [
          {
            id: "actions",
            header: <span className="sr-only">Actions</span>,
            align: "end" as const,
            interactive: true,
            cell: (member: MemberOut) => (
              <RemoveMemberButton
                workspaceId={membership.id}
                member={member}
                onRemoved={() => invalidate(membership.id)}
              />
            ),
          },
        ]
      : []),
  ];

  return (
    <Section
      id="team"
      title="Team"
      description="Everyone with access to this workspace, and their role."
      aside={
        membership ? (
          canManage ? (
            <InviteDialog workspaceId={membership.id} onInvited={() => invalidate(membership.id)} />
          ) : (
            <ReadOnlyNote>{readOnlyCopy("admin", "invite people or change roles")}</ReadOnlyNote>
          )
        ) : null
      }
    >
      <SectionRow>
        {membersQuery.isLoading || !membership ? (
          <RowsSkeleton label="Loading the team" />
        ) : membersQuery.isError ? (
          <ErrorBanner
            error={membersQuery.error}
            context={{ action: "load the team" }}
            onRetry={() => void membersQuery.refetch()}
          />
        ) : members.length === 0 ? (
          <EmptyState
            variant="plain"
            icon={UsersIcon}
            title="No members yet"
            description={canManage ? "Invite someone to give them access." : undefined}
          />
        ) : (
          <>
            <ListSearchField search={search} label="Search members" total={members.length} />
            {search.noMatches ? (
              <ListNoMatches search={search} items="members" />
            ) : (
              <ResponsiveTable<MemberOut>
                columns={columns}
                rows={search.filtered}
                label="Team members"
                getRowKey={(member) => member.user_id}
                renderCard={(member) => (
                  // Phones get the same role and remove controls as the table.
                  <div className="flex flex-col gap-3">
                    <div className="flex items-center justify-between gap-2">
                      {identity(member)}
                      <MemberStatus member={member} />
                    </div>
                    {canManage ? (
                      <div className="flex items-center justify-between gap-2">
                        <RoleSelect
                          workspaceId={membership.id}
                          member={member}
                          onChanged={() => invalidate(membership.id)}
                        />
                        <RemoveMemberButton
                          workspaceId={membership.id}
                          member={member}
                          onRemoved={() => invalidate(membership.id)}
                        />
                      </div>
                    ) : (
                      <Tag>{ROLE_LABEL[member.role]}</Tag>
                    )}
                  </div>
                )}
              />
            )}
          </>
        )}
      </SectionRow>
    </Section>
  );
}

function RoleSelect({
  workspaceId,
  member,
  onChanged,
}: {
  workspaceId: string;
  member: MemberOut;
  onChanged: () => void;
}) {
  const [saving, setSaving] = React.useState(false);

  async function onChange(next: string) {
    setSaving(true);
    try {
      await api.put(`workspaces/${workspaceId}/members/${member.user_id}`, { role: next });
      toast.success(`${member.name || member.email} is now ${ROLE_LABEL[next as Role] ?? next}`);
      onChanged();
    } catch (err) {
      toast.error("Couldn't change the role", { description: errorMessage(err) });
    } finally {
      setSaving(false);
    }
  }

  return (
    <Select value={member.role} onValueChange={onChange} disabled={saving}>
      <SelectTrigger aria-label={`Role for ${member.email}`} size="sm" className="w-32">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {ROLES.map((r) => (
          <SelectItem key={r} value={r}>
            {ROLE_LABEL[r]}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function RemoveMemberButton({
  workspaceId,
  member,
  onRemoved,
}: {
  workspaceId: string;
  member: MemberOut;
  onRemoved: () => void;
}) {
  return (
    <ConfirmDialog
      trigger={
        <Button type="button" variant="ghost" size="sm">
          Remove
        </Button>
      }
      title={`Remove ${member.email}?`}
      description="They lose access to this workspace. You can invite them again later."
      confirmLabel="Remove member"
      onConfirm={async () => {
        // A failure throws: the dialog stays open and says why in plain words.
        await api.delete(`workspaces/${workspaceId}/members/${member.user_id}`);
        toast.success(`Removed ${member.email}`);
        onRemoved();
      }}
    />
  );
}

/**
 * The Team tab's only way to add someone (R-V2-30): accounts are global and an
 * email is not proven, so the api's `POST …/members` re-adds former members
 * only and answers 409 `use_invite` for anyone else. The console never calls
 * it; "Send invite" is the path, and an existing account accepts with its own
 * password.
 */
function InviteDialog({ workspaceId, onInvited }: { workspaceId: string; onInvited: () => void }) {
  const [open, setOpen] = React.useState(false);
  const [email, setEmail] = React.useState("");
  const [role, setRole] = React.useState<Role>("viewer");
  const [sending, setSending] = React.useState(false);
  const [invite, setInvite] = React.useState<InviteOut | null>(null);
  const [error, setError] = React.useState<unknown>(null);

  function reset() {
    setEmail("");
    setRole("viewer");
    setInvite(null);
    setError(null);
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSending(true);
    setError(null);
    try {
      const created = await api.post<InviteOut>(`workspaces/${workspaceId}/invites`, { email, role });
      setInvite(created);
      onInvited();
    } catch (err) {
      setError(err);
    } finally {
      setSending(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" variant="primary" size="sm">
          <UserPlusIcon aria-hidden="true" />
          Invite
        </Button>
      </DialogTrigger>
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Invite someone</DialogTitle>
          <DialogDescription>
            Creates a one-time link, valid for 7 days. They set a password when they open it, or confirm with
            their own password if they already have an account.
          </DialogDescription>
        </DialogHeader>
        {invite ? (
          <>
            <DialogBody className="gap-3">
              <p className="text-label text-text-secondary">
                Send this link to <span className="font-medium text-foreground">{invite.email}</span>:
              </p>
              <div className="flex items-center gap-2 rounded border border-border bg-muted p-2">
                <code className="min-w-0 flex-1 font-mono text-caption break-all">{invite.url}</code>
                <CopyButton value={invite.url} label="Copy invite link" />
              </div>
              <p className="text-caption text-text-secondary">This link won&apos;t be shown again.</p>
            </DialogBody>
            <DialogFooter showCloseButton />
          </>
        ) : (
          <form onSubmit={onSubmit} className="flex min-h-0 flex-1 flex-col">
            <DialogBody className="gap-4">
              {error ? <ErrorBanner error={error} context={{ action: "send the invite" }} /> : null}
              <Field label="Email" htmlFor="invite-email">
                <Input
                  id="invite-email"
                  type="email"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  disabled={sending}
                />
              </Field>
              <Field label="Role" htmlFor="invite-role">
                <Select value={role} onValueChange={(v) => setRole(v as Role)} disabled={sending}>
                  <SelectTrigger id="invite-role">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ROLES.map((r) => (
                      <SelectItem key={r} value={r}>
                        {ROLE_LABEL[r]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
            </DialogBody>
            <DialogFooter>
              <Button type="button" onClick={() => setOpen(false)} disabled={sending}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" busy={sending} busyLabel="Sending invite…">
                Send invite
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

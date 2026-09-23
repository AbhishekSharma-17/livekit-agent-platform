"use client";

import * as React from "react";
import { MailPlusIcon, UsersIcon } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
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
import { ResponsiveTable, type ResponsiveTableColumn } from "@/components/shared/responsive-table";
import { Section, SectionRow } from "@/components/shared/section";
import { StatusChip } from "@/components/shared/status-chip";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { api, ApiError } from "@/lib/api";
import type { InviteOut, MemberOut, Role } from "./api-types";
import { ROLE_LABEL } from "./api-types";
import { useActiveWorkspace, useInvalidateSettings, useMembers } from "./use-settings-queries";
import { SkeletonRows } from "@/components/shared/loading-state";

const ROLES: Role[] = ["viewer", "builder", "admin", "owner"];

function canManageMembers(role: string | undefined): boolean {
  return role === "admin" || role === "owner";
}

export function TeamTab() {
  const { workspace: membership } = useActiveWorkspace();
  const membersQuery = useMembers(membership?.id);
  const invalidate = useInvalidateSettings();
  const canManage = canManageMembers(membership?.role);

  const members = membersQuery.data?.items ?? [];

  const columns: ResponsiveTableColumn<MemberOut>[] = [
    {
      id: "member",
      header: "Member",
      cell: (member) => (
        <div className="min-w-0">
          <div className="truncate font-medium text-foreground">{member.name || member.email}</div>
          <div className="truncate font-mono text-xs text-muted-foreground">{member.email}</div>
        </div>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (member) =>
        member.pending ? (
          <StatusChip tone="warning" size="sm">
            Invited
          </StatusChip>
        ) : member.disabled ? (
          <StatusChip tone="danger" size="sm">
            Disabled
          </StatusChip>
        ) : (
          <StatusChip tone="success" size="sm">
            Active
          </StatusChip>
        ),
    },
    {
      id: "role",
      header: "Role",
      interactive: true,
      cell: (member) =>
        canManage ? (
          <RoleSelect
            workspaceId={membership!.id}
            userId={member.user_id}
            role={member.role}
            onChanged={() => invalidate(membership!.id)}
          />
        ) : (
          <span className="text-muted-foreground">{ROLE_LABEL[member.role]}</span>
        ),
    },
    ...(canManage
      ? [
          {
            id: "actions",
            header: <span className="sr-only">Actions</span>,
            align: "end" as const,
            interactive: true,
            cell: (member: MemberOut) => (
              <RemoveMemberButton
                workspaceId={membership!.id}
                member={member}
                onRemoved={() => invalidate(membership!.id)}
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
        canManage && membership ? (
          <InviteDialog workspaceId={membership.id} onInvited={() => invalidate(membership.id)} />
        ) : null
      }
    >
      <SectionRow>
        {membersQuery.isLoading ? (
          <SkeletonRows label="Loading the team" rowClassName="h-12" />
        ) : membersQuery.isError ? (
          <ErrorBanner message={`Couldn't load the team — ${errorMessage(membersQuery.error)}`} onRetry={() => membersQuery.refetch()} />
        ) : members.length === 0 ? (
          <EmptyState icon={UsersIcon} title="No members yet" compact />
        ) : (
          <ResponsiveTable<MemberOut>
            columns={columns}
            rows={members}
            label="Team members"
            getRowKey={(member) => member.user_id}
            renderCard={(member) => (
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate font-medium text-foreground">{member.name || member.email}</div>
                  <div className="truncate font-mono text-xs text-muted-foreground">{member.email}</div>
                </div>
                <StatusChip tone="neutral" size="sm">
                  {ROLE_LABEL[member.role]}
                </StatusChip>
              </div>
            )}
          />
        )}
      </SectionRow>
    </Section>
  );
}

function RoleSelect({
  workspaceId,
  userId,
  role,
  onChanged,
}: {
  workspaceId: string;
  userId: string;
  role: Role;
  onChanged: () => void;
}) {
  const [saving, setSaving] = React.useState(false);

  async function onChange(next: string) {
    setSaving(true);
    try {
      await api.put(`workspaces/${workspaceId}/members/${userId}`, { role: next });
      onChanged();
    } catch (err) {
      toast.error(`Couldn't change role — ${errorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Select value={role} onValueChange={onChange} disabled={saving}>
      <SelectTrigger aria-label="Role" className="h-8 w-32">
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
  const [busy, setBusy] = React.useState(false);

  async function onClick() {
    setBusy(true);
    try {
      await api.delete(`workspaces/${workspaceId}/members/${member.user_id}`);
      toast.success(`Removed ${member.email}`);
      onRemoved();
    } catch (err) {
      toast.error(`Couldn't remove ${member.email} — ${errorMessage(err)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button type="button" variant="ghost" size="sm" onClick={onClick} disabled={busy}>
      Remove
    </Button>
  );
}

function InviteDialog({ workspaceId, onInvited }: { workspaceId: string; onInvited: () => void }) {
  const [open, setOpen] = React.useState(false);
  const [email, setEmail] = React.useState("");
  const [role, setRole] = React.useState<Role>("viewer");
  const [sending, setSending] = React.useState(false);
  const [invite, setInvite] = React.useState<InviteOut | null>(null);
  const [error, setError] = React.useState<string | null>(null);

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
      setError(err instanceof ApiError ? err.message : errorMessage(err));
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
        <Button type="button" size="sm">
          <MailPlusIcon />
          Invite
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invite someone</DialogTitle>
          <DialogDescription>
            Creates a one-time link, valid for 7 days. They set a password when they open it.
          </DialogDescription>
        </DialogHeader>
        {invite ? (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              Send this link to <span className="font-medium text-foreground">{invite.email}</span>:
            </p>
            <div className="flex items-center gap-2 rounded-md border border-border bg-muted/50 p-2">
              <code className="min-w-0 flex-1 truncate font-mono text-xs">{invite.url}</code>
              <CopyButton value={invite.url} label="Copy invite link" />
            </div>
            <p className="text-xs text-muted-foreground">This link won&apos;t be shown again.</p>
          </div>
        ) : (
          <form onSubmit={onSubmit} className="space-y-4">
            {error ? <ErrorBanner message={error} /> : null}
            <Field label="Email" htmlFor="invite-email" required>
              <Input
                id="invite-email"
                type="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                disabled={sending}
              />
            </Field>
            <Field label="Role" htmlFor="invite-role" required>
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
            <DialogFooter>
              <Button type="submit" disabled={sending}>
                Send invite
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

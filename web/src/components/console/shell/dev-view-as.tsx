"use client";

import * as React from "react";

import { Tag } from "@/components/shared/tag";
import { SimpleSelect } from "@/components/ui/select";
import {
  DEV_VIEW_AS_ROLES,
  devViewAsAllowed,
  setDevViewAs,
  useDevViewAsChoice,
  viewAsRole,
  type Role,
} from "@/components/console/lib/dev-view-as";
import { ROLE_LABEL } from "@/components/console/settings/api-types";
import { useMe } from "./use-me";

const RANK: Record<Role, number> = { viewer: 0, builder: 1, admin: 2, owner: 3 };

/** The real role the switch lowers from: the admin bypass is an owner in every workspace. */
function realRoleOf(me: ReturnType<typeof useMe>["me"]): Role {
  return me?.workspaces?.[0]?.role ?? "owner";
}

/**
 * The account menu's "Development" group (decision O6): pick the role the
 * console renders for. Rendered only in development under the admin bypass
 * (`devViewAsAllowed`); the choice is kept per browser and changes nothing on
 * the server.
 */
export function DevViewAsMenuGroup() {
  const { me } = useMe();
  const choice = useDevViewAsChoice();
  const labelId = React.useId();
  const hintId = React.useId();
  if (!devViewAsAllowed(me?.user.id)) return null;
  const real = realRoleOf(me);
  const current = viewAsRole(real, choice, true);
  const options = DEV_VIEW_AS_ROLES.filter((role) => RANK[role] <= RANK[real]).map((role) => ({
    value: role,
    label: role === real ? `${ROLE_LABEL[role]} (your role)` : ROLE_LABEL[role],
  }));
  return (
    <div role="group" aria-labelledby={labelId} data-slot="dev-view-as" className="flex flex-col gap-1.5 px-2 py-1.5">
      <p id={labelId} className="text-caption font-medium text-text-tertiary">
        Development
      </p>
      <div className="flex items-center justify-between gap-3">
        <span className="text-control text-foreground">View as</span>
        <SimpleSelect
          size="sm"
          value={current}
          onValueChange={(next) => setDevViewAs(next === real ? null : (next as Role))}
          options={options}
          aria-label="View the console as"
          aria-describedby={hintId}
        />
      </div>
      <p id={hintId} className="text-caption text-pretty text-text-secondary">
        Changes what the console shows, not what the server allows.
      </p>
    </div>
  );
}

/**
 * A reminder in the top bar while the console renders for a lower role than
 * the real one, so a developer never mistakes it for a real viewer's session.
 */
export function DevViewAsBadge() {
  const { me } = useMe();
  const choice = useDevViewAsChoice();
  if (!devViewAsAllowed(me?.user.id)) return null;
  const real = realRoleOf(me);
  const shown = viewAsRole(real, choice, true);
  if (shown === real) return null;
  return (
    <Tag data-slot="dev-view-as-badge" data-role={shown} className="shrink-0">
      Viewing as {ROLE_LABEL[shown].toLowerCase()} (development)
    </Tag>
  );
}

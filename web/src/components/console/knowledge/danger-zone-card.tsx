"use client";

import * as React from "react";
import { Trash2Icon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { TypedConfirmDialog } from "@/components/console/shared/confirm-dialog";

export interface DangerZoneCardProps {
  /** What the button does: "Delete knowledge base". */
  actionLabel: string;
  /** One sentence on what happens, shown on the card. */
  description: React.ReactNode;
  /** The confirmation's title, naming the object: "Delete “Policies”?". */
  confirmTitle: string;
  /** Exactly what will happen, inside the confirmation. */
  confirmDescription: React.ReactNode;
  /** What will be removed, listed inside the confirmation. */
  children?: React.ReactNode;
  onConfirm: () => Promise<void>;
}

/**
 * The detail page's Danger zone (docs/ui/DESIGN-SYSTEM.md section 7.4, detail
 * archetype): the last card of the side column, a danger-outline entry point
 * and a typed confirmation (type DELETE) that lists what will be removed.
 * Render it only for people who may take the action (decision D12).
 */
export function DangerZoneCard({
  actionLabel,
  description,
  confirmTitle,
  confirmDescription,
  children,
  onConfirm,
}: DangerZoneCardProps) {
  return (
    <Card data-slot="danger-zone" className="border-destructive-border">
      <CardHeader>
        <CardTitle>
          <h2>Danger zone</h2>
        </CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <TypedConfirmDialog
          trigger={
            <Button type="button" variant="danger-outline">
              <Trash2Icon aria-hidden="true" /> {actionLabel}
            </Button>
          }
          title={confirmTitle}
          description={confirmDescription}
          confirmText="DELETE"
          confirmLabel={actionLabel}
          onConfirm={onConfirm}
        >
          {children}
        </TypedConfirmDialog>
      </CardContent>
    </Card>
  );
}

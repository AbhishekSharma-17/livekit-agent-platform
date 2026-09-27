"use client";

/**
 * `cards` block — rich cards the caller compares and picks from, e.g. cover
 * plans or repair shops (`show_cards`, `CardsBlockState`, V5-43 → V5-44,
 * `docs/v5/_asks.md` #310).
 *
 * Tapping a card (when `selectable`) sends `block_action {name: "select",
 * data: {card_id}}`; each of its `actions[]` buttons sends `block_action
 * {name: <action.name>, data: {card_id}}`. A card's own tap target and its
 * action buttons are siblings, never nested `<button>`s.
 *
 * A picture comes from `image_asset_id` (a session asset, `panel.assets`)
 * or `image_url` — but only when it is `https://` on one of the block
 * config's `image_hosts` (`checkedHttpsUrl`, `./types`, re-checked here the
 * same way `link.tsx` re-checks its own url: an empty `image_hosts` allows
 * no `image_url` at all, session-asset pictures only, per the contract).
 * Every `image_url` picture loads `loading="lazy"` and never leaks a
 * referrer to the image host.
 */
import * as React from "react";

import { StatusChip } from "@/components/shared/status-chip";
import { Button } from "@/components/ui/button";
import type { Card as CardData, CardAction, CardFact, CardsBlockState } from "@/contracts/lkap-contracts";
import { cn } from "@/lib/utils";
import { PanelEmpty } from "@/panels/generic/blocks";

import { BlockFrame } from "./frame";
import { checkedHttpsUrl, hostsOf } from "./types";
import type { BlockRenderProps } from "./types";

type CardsLayout = "carousel" | "grid" | "list";
type ActionTone = NonNullable<CardAction["tone"]>;

const ACTION_VARIANT: Record<ActionTone, "destructive" | "default" | "outline"> = {
  danger: "destructive",
  success: "default",
  neutral: "outline",
  info: "outline",
  warning: "outline",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function layoutOf(config: unknown): CardsLayout {
  const layout = isRecord(config) ? config.layout : undefined;
  return layout === "grid" || layout === "list" ? layout : "carousel";
}

function selectableOf(config: unknown): boolean {
  return isRecord(config) ? config.selectable !== false : true;
}

const LIST_CLASS: Record<CardsLayout, string> = {
  carousel: "flex snap-x snap-mandatory gap-3 overflow-x-auto pb-1",
  grid: "grid grid-cols-2 gap-3",
  list: "flex flex-col gap-3",
};

const ITEM_CLASS: Record<CardsLayout, string> = {
  carousel: "w-56 shrink-0 snap-start",
  grid: "",
  list: "",
};

function actionsOf(card: CardData): CardAction[] {
  return Array.isArray(card.actions) ? (card.actions as unknown as CardAction[]) : [];
}

function factsOf(card: CardData): CardFact[] {
  return Array.isArray(card.facts) ? (card.facts as unknown as CardFact[]) : [];
}

function badgesOf(card: CardData): string[] {
  return Array.isArray(card.badges) ? (card.badges as unknown as string[]) : [];
}

function CardImage({ url, alt }: { url: string; alt: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element -- an https:// picture from an allowed site, checked above
    <img
      src={url}
      alt={alt}
      loading="lazy"
      referrerPolicy="no-referrer"
      className="aspect-video w-full rounded-md object-cover"
    />
  );
}

function CardTile({
  card,
  selectable,
  selected,
  onSelect,
  onAction,
  imageUrl,
}: {
  card: CardData;
  selectable: boolean;
  selected: boolean;
  onSelect: () => void;
  onAction: (name: string) => void;
  imageUrl: string | undefined;
}) {
  const facts = factsOf(card);
  const badges = badgesOf(card);
  const actions = actionsOf(card);

  const body = (
    <div className="flex flex-col gap-2 p-3">
      {imageUrl ? <CardImage url={imageUrl} alt={card.title} /> : null}
      {badges.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {badges.map((badge) => (
            <StatusChip key={badge} tone="info" size="sm">
              {badge}
            </StatusChip>
          ))}
        </div>
      )}
      <div>
        <p className="text-sm font-semibold">{card.title}</p>
        {card.subtitle && <p className="text-muted-foreground text-[0.8125rem]">{card.subtitle}</p>}
      </div>
      {facts.length > 0 && (
        <dl className="flex flex-col gap-1">
          {facts.map((fact, index) => (
            <div key={`${fact.label}-${index}`} className="flex items-baseline justify-between gap-2 text-[0.8125rem]">
              <dt className="text-muted-foreground">{fact.label}</dt>
              <dd className="font-medium">{fact.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );

  return (
    <article
      data-slot="block-card"
      data-selected={selected ? "true" : undefined}
      className={cn(
        "border-border bg-card overflow-hidden rounded-lg border",
        selected && "ring-brand ring-2 ring-inset",
      )}
    >
      {selectable ? (
        <button
          type="button"
          aria-pressed={selected}
          aria-label={`${selected ? "Selected: " : "Choose "}${card.title}`}
          onClick={onSelect}
          className="focus-visible:ring-ring block w-full text-left focus-visible:ring-2 focus-visible:outline-none"
        >
          {body}
        </button>
      ) : (
        body
      )}
      {actions.length > 0 && (
        <div className="flex flex-wrap gap-2 px-3 pb-3">
          {actions.map((action) => (
            <Button
              key={action.name}
              type="button"
              size="sm"
              variant={action.tone ? ACTION_VARIANT[action.tone] : "outline"}
              onClick={() => onAction(action.name)}
            >
              {action.label}
            </Button>
          ))}
        </div>
      )}
    </article>
  );
}

export function CardsBlock({ spec, data, panel, title, highlighted }: BlockRenderProps<CardsBlockState>) {
  const layout = layoutOf(spec.config);
  const selectable = selectableOf(spec.config);
  const imageHosts = hostsOf(spec.config, "image_hosts");
  const cards = Array.isArray(data.cards) ? (data.cards as unknown as CardData[]) : [];

  function imageUrlFor(card: CardData): string | undefined {
    if (card.image_asset_id) {
      const fromAsset = panel.assets.get(card.image_asset_id);
      if (fromAsset) return fromAsset;
    }
    return checkedHttpsUrl(card.image_url, imageHosts) ?? undefined;
  }

  function perform(cardId: string, name: string, extra?: Record<string, unknown>) {
    void panel.perform({
      action: "block_action",
      payload: { block_id: spec.id, name, data: { card_id: cardId, ...extra } },
    });
  }

  if (cards.length === 0) {
    return (
      <BlockFrame spec={spec} title={title} highlighted={highlighted}>
        <PanelEmpty>The agent will show cards here when it has options to compare.</PanelEmpty>
      </BlockFrame>
    );
  }

  return (
    <BlockFrame spec={spec} title={title} count={cards.length} highlighted={highlighted}>
      <ul data-slot="block-cards" className={LIST_CLASS[layout]}>
        {cards.map((card) => (
          <li key={card.id} className={ITEM_CLASS[layout]}>
            <CardTile
              card={card}
              selectable={selectable}
              selected={data.selected === card.id}
              onSelect={() => perform(card.id, "select")}
              onAction={(name) => perform(card.id, name)}
              imageUrl={imageUrlFor(card)}
            />
          </li>
        ))}
      </ul>
    </BlockFrame>
  );
}

export default CardsBlock;

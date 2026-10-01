"use client";

import * as React from "react";
import {
  ArrowRightIcon,
  ClockIcon,
  CoinsIcon,
  DownloadIcon,
  KeyRoundIcon,
  PencilIcon,
  PhoneIcon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
  UsersIcon,
} from "lucide-react";
import { AGENTS_ICON } from "@/components/console/shell/nav-config";
import { toast } from "sonner";

import { ConfirmDialog, TypedConfirmDialog } from "@/components/console/shared/confirm-dialog";
import { ErrorBanner } from "@/components/console/shared/error-banner";
import { ChipInput, validateEmail } from "@/components/shared/chip-input";
import { CheckboxRow, OptionCard, RadioRow, SwitchRow } from "@/components/shared/choice";
import { Avatar, MetaList, StatCard, StatGrid } from "@/components/shared/data-display";
import { EmptyState, NoMatches } from "@/components/shared/empty-state";
import { Field, FieldRow, FormError } from "@/components/shared/field";
import { FileInput } from "@/components/shared/file-input";
import { Kbd } from "@/components/shared/kbd";
import { ListCard, ListCardRow } from "@/components/shared/list-card";
import { LoadingRegion, LoadingRow } from "@/components/shared/loading-state";
import { Page, PageHeader } from "@/components/shared/page-header";
import { PasswordInput, SecretInput } from "@/components/shared/password-input";
import { ProgressSteps } from "@/components/shared/progress-steps";
import { ReadOnlyNote } from "@/components/shared/read-only-note";
import { RowMenu } from "@/components/shared/row-menu";
import { InputWithIcon, SearchField } from "@/components/shared/search-field";
import { SegmentedControl } from "@/components/shared/segmented-control";
import { LifecycleBadge, StatusPill } from "@/components/shared/status-chip";
import { Tag, TagList } from "@/components/shared/tag";
import { ThemeSwitcher } from "@/components/shared/theme-switcher";
import { Alert, type AlertTone } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, IconButton } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardInset, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverDescription, PopoverHeader, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { Combobox } from "@/components/ui/searchable-select";
import { SimpleSelect } from "@/components/ui/select";
import { SkeletonText, Skeleton } from "@/components/ui/skeleton";
import { Toaster } from "@/components/ui/sonner";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsCount, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { ThemePreference } from "@/lib/theme";

/**
 * The styleguide (docs/ui/DESIGN-SYSTEM.md section 11): every primitive, in
 * both themes, for people reviewing the system. Served at
 * `/console/preview/styleguide` (the shell-less preview route group).
 *
 * The theme toggle previews light or dark by switching the `dark` class on
 * `<html>` for this page only; it restores the original class on leave and
 * never writes the person's stored preference.
 */

type PreviewTheme = "light" | "dark";

function usePreviewTheme(theme: PreviewTheme) {
  React.useEffect(() => {
    const root = document.documentElement;
    const hadDark = root.classList.contains("dark");
    const previousScheme = root.style.colorScheme;
    root.classList.toggle("dark", theme === "dark");
    root.style.colorScheme = theme;
    return () => {
      root.classList.toggle("dark", hadDark);
      root.style.colorScheme = previousScheme;
    };
  }, [theme]);
}

function Specimen({ id, title, description, children }: { id: string; title: string; description?: string; children: React.ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="flex scroll-mt-6 flex-col gap-3.5">
      <div>
        <h2 id={`${id}-title`} className="text-title font-semibold tracking-[-0.008em]">
          {title}
        </h2>
        {description ? <p className="mt-0.5 max-w-[72ch] text-label text-text-secondary">{description}</p> : null}
      </div>
      {children}
    </section>
  );
}

function Row({ label, children }: { label?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      {label ? <div className="text-caption text-text-tertiary">{label}</div> : null}
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

const NEUTRALS = [
  ["--background", "Main working panel"],
  ["--card", "Cards, inputs"],
  ["--popover", "Dialogs, menus"],
  ["--muted", "Hover fills, footers"],
  ["--muted-strong", "Switch track, avatar"],
  ["--border", "Every hairline"],
  ["--border-strong", "Input hover"],
  ["--input", "Input borders"],
  ["--sidebar", "App background"],
] as const;

const TEXT = [
  ["--foreground", "Primary text"],
  ["--text-secondary", "Descriptions"],
  ["--text-tertiary", "Labels, meta"],
  ["--text-disabled", "Disabled"],
] as const;

const ACCENT = [
  ["--brand", "Primary action, links"],
  ["--brand-hover", "Hover"],
  ["--brand-active", "Pressed"],
  ["--brand-subtle", "Selected fill"],
  ["--brand-border", "Selected edge"],
  ["--ring", "Focus ring"],
] as const;

const STATUS_SCALES = ["success", "warning", "info", "destructive"] as const;

function Swatch({ token, role }: { token: string; role: string }) {
  return (
    <div className="flex items-center gap-3">
      <span aria-hidden="true" className="size-9 shrink-0 rounded border border-border" style={{ background: `var(${token})` }} />
      <span className="flex min-w-0 flex-col">
        <code className="truncate font-mono text-caption text-foreground">{token}</code>
        <span className="text-caption text-text-tertiary">{role}</span>
      </span>
    </div>
  );
}

const TYPE_SCALE = [
  ["text-display", "Sign-in title · 26 px / 600", "text-display font-semibold tracking-[-0.025em]"],
  ["text-page", "Page title · 22 px / 600", "text-page font-semibold tracking-[-0.018em]"],
  ["text-stat", "Stat value · 24 px / 600", "text-stat font-semibold tracking-[-0.02em] tabular-nums"],
  ["text-dialog", "Dialog title · 17 px / 600", "text-dialog font-semibold tracking-[-0.012em]"],
  ["text-title", "Card or section title · 15 px / 600", "text-title font-semibold tracking-[-0.008em]"],
  ["text-body", "Body · 14 px / 400", "text-body"],
  ["text-control", "Buttons, nav, menu items · 13.5 px / 500", "text-control font-medium"],
  ["text-label", "Labels, table cells, alerts · 13 px", "text-label"],
  ["text-caption", "Hints, badges, meta · 12 px", "text-caption text-text-secondary"],
  ["eyebrow", "Eyebrow · 12 px / 500 · the only uppercase", "text-caption font-medium tracking-[0.04em] text-text-tertiary uppercase"],
] as const;

const MODEL_GROUPS = [
  { heading: "Current", options: [{ value: "gpt-4o-mini", label: "gpt-4o-mini" }] },
  {
    heading: "Recommended",
    options: [
      { value: "claude-haiku", label: "Claude Haiku" },
      { value: "gemini-flash", label: "Gemini Flash" },
    ],
  },
  {
    heading: "All",
    options: Array.from({ length: 140 }, (_, i) => ({ value: `model-${i + 1}`, label: `Model ${i + 1}` })),
  },
];

const ALERT_TONES: AlertTone[] = ["info", "success", "warning", "danger", "brand", "neutral"];

export function Styleguide() {
  const [theme, setTheme] = React.useState<PreviewTheme>("light");
  usePreviewTheme(theme);

  const [search, setSearch] = React.useState("");
  const [emails, setEmails] = React.useState<string[]>(["ada@example.com", "not-an-email"]);
  const [secret, setSecret] = React.useState("");
  const [trunk, setTrunk] = React.useState("");
  const [model, setModel] = React.useState("gpt-4o-mini");
  const [filter, setFilter] = React.useState("all");
  const [switchOn, setSwitchOn] = React.useState(true);
  const [themeChoice, setThemeChoice] = React.useState<ThemePreference>("system");

  return (
    <TooltipProvider>
      <div className="min-h-dvh bg-background text-foreground">
        <Page>
          <PageHeader
            eyebrow="Design system"
            title="Styleguide"
            description="Every primitive the console is built from, in both themes."
            actions={
              <SegmentedControl
                label="Preview theme"
                value={theme}
                onValueChange={setTheme}
                options={[
                  { value: "light", label: "Light" },
                  { value: "dark", label: "Dark" },
                ]}
              />
            }
          />

          <div className="flex flex-col gap-8">
            <Specimen id="tokens" title="Colour tokens" description="Components read colours only through these tokens. Status colours are a separate system from the accent.">
              <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
                <div className="flex flex-col gap-2.5">
                  <div className="text-caption text-text-tertiary">Neutrals</div>
                  {NEUTRALS.map(([token, role]) => (
                    <Swatch key={token} token={token} role={role} />
                  ))}
                </div>
                <div className="flex flex-col gap-2.5">
                  <div className="text-caption text-text-tertiary">Text</div>
                  {TEXT.map(([token, role]) => (
                    <Swatch key={token} token={token} role={role} />
                  ))}
                </div>
                <div className="flex flex-col gap-2.5">
                  <div className="text-caption text-text-tertiary">Accent</div>
                  {ACCENT.map(([token, role]) => (
                    <Swatch key={token} token={token} role={role} />
                  ))}
                </div>
                <div className="flex flex-col gap-2.5">
                  <div className="text-caption text-text-tertiary">Status scales (solid, text, subtle, border)</div>
                  {STATUS_SCALES.map((scale) => (
                    <div key={scale} className="flex items-center gap-1.5">
                      {["solid", "text", "subtle", "border"].map((part) => (
                        <span
                          key={part}
                          aria-hidden="true"
                          title={`--${scale}-${part}`}
                          className="size-7 rounded-sm border border-border"
                          style={{ background: `var(--${scale}-${part})` }}
                        />
                      ))}
                      <code className="ml-1 font-mono text-caption">{scale}</code>
                    </div>
                  ))}
                </div>
              </div>
            </Specimen>

            <Specimen id="type" title="Type scale" description="Hierarchy through weight and tone, not size. Figures are tabular.">
              <Card>
                <CardContent className="flex flex-col gap-3 pt-5">
                  {TYPE_SCALE.map(([name, role, classes]) => (
                    <div key={name} className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-6">
                      <code className="w-32 shrink-0 font-mono text-caption text-text-tertiary">{name}</code>
                      <span className={classes}>{role}</span>
                    </div>
                  ))}
                </CardContent>
              </Card>
              <Row label="Radius, elevation and focus">
                <span className="flex h-12 w-24 items-center justify-center rounded-sm border border-border bg-card text-caption">sm 6</span>
                <span className="flex h-12 w-24 items-center justify-center rounded border border-border bg-card text-caption">default 8</span>
                <span className="flex h-12 w-24 items-center justify-center rounded-lg border border-border bg-card text-caption">lg 12</span>
                <span className="flex h-12 w-24 items-center justify-center rounded-dialog border border-border bg-card text-caption">dialog 14</span>
                <span className="flex h-12 w-24 items-center justify-center rounded-lg bg-popover text-caption shadow-overlay">overlay</span>
                <span className="flex h-12 w-24 items-center justify-center rounded-lg bg-popover text-caption shadow-modal">modal</span>
                <span className="flex h-12 w-24 items-center justify-center rounded border border-brand bg-card text-caption shadow-focus">focus</span>
              </Row>
            </Specimen>

            <Specimen id="buttons" title="Buttons" description="One primary per view, placed last. A variant-less button is secondary. Busy buttons change their label.">
              <Row label="Variants">
                <Button variant="ghost">Ghost</Button>
                <Button variant="danger-outline">
                  <Trash2Icon /> Delete agent
                </Button>
                <Button variant="secondary">Secondary</Button>
                <Button variant="primary">Primary</Button>
              </Row>
              <Row label="Inside a confirmation only">
                <Button variant="danger">Delete</Button>
              </Row>
              <Row label="Sizes">
                <Button size="sm">Small</Button>
                <Button>Default</Button>
                <Button size="lg" variant="primary">
                  Large
                </Button>
                <Button size="icon" aria-label="Add">
                  <PlusIcon />
                </Button>
              </Row>
              <Row label="States">
                <Button disabled>Disabled</Button>
                <Button variant="primary" busy busyLabel="Saving…">
                  Save
                </Button>
                <IconButton label="Refresh" busy>
                  <RefreshCwIcon />
                </IconButton>
                <IconButton label="Edit">
                  <PencilIcon />
                </IconButton>
                <RowMenu
                  label="More actions"
                  actions={[
                    { label: "Rename", icon: PencilIcon, onSelect: () => toast.info("Rename chosen.") },
                    { label: "Download", icon: DownloadIcon, onSelect: () => toast.info("Download chosen.") },
                  ]}
                  destructive={{ label: "Delete", icon: Trash2Icon, onSelect: () => toast.warning("Delete chosen.") }}
                />
              </Row>
              <Row label="Text buttons">
                <Button variant="link">View all sessions</Button>
                <Button variant="link-neutral">Skip for now</Button>
                <Button variant="link-destructive">Remove</Button>
              </Row>
              <Row label="Block">
                <Button size="block" variant="primary" className="max-w-sm">
                  Continue
                </Button>
              </Row>
            </Specimen>

            <Specimen id="status" title="Status, badges and tags" description="Status is always a word plus a tone, from one shared lifecycle map.">
              <Row label="Status pills">
                <StatusPill tone="success">Ready</StatusPill>
                <StatusPill tone="warning">Ready to review</StatusPill>
                <StatusPill tone="info">In progress</StatusPill>
                <StatusPill tone="danger">Needs attention</StatusPill>
                <StatusPill tone="live">Live</StatusPill>
                <StatusPill tone="neutral">Stopped</StatusPill>
              </Row>
              <Row label="From api states">
                {["published", "draft", "indexing", "join_failed", "active", "cancelled"].map((state) => (
                  <LifecycleBadge key={state} state={state} />
                ))}
              </Row>
              <Row label="Badges and tags">
                <Badge tone="brand">New</Badge>
                <Badge tone="neutral">12 voices</Badge>
                <Badge tone="warning">Beta</Badge>
                <TagList>
                  <Tag>Billing</Tag>
                  <Tag>12 Sep</Tag>
                  <Tag>Claims</Tag>
                </TagList>
              </Row>
            </Specimen>

            <Specimen id="fields" title="Fields" description="Labels are 13 px, hints 12 px, errors say what to fix. Mark optional fields, not required ones.">
              <Card>
                <CardContent className="flex flex-col gap-4 pt-5">
                  <FieldRow>
                    <Field label="Agent name" htmlFor="sg-name" hint="Callers hear this name.">
                      <Input id="sg-name" defaultValue="Front desk" />
                    </Field>
                    <Field label="Description" htmlFor="sg-desc" optional>
                      <Input id="sg-desc" placeholder="What this agent handles" />
                    </Field>
                  </FieldRow>
                  <Field label="Slug" htmlFor="sg-slug" error="That slug is already taken. Choose another.">
                    <Input id="sg-slug" defaultValue="front-desk" />
                  </Field>
                  <Field label="Greeting" htmlFor="sg-greeting">
                    <Textarea id="sg-greeting" defaultValue="Hi, thanks for calling. How can I help?" />
                  </Field>
                  <FieldRow>
                    <Field label="Search" htmlFor="sg-search">
                      <SearchField id="sg-search" aria-label="Search agents" value={search} onValueChange={setSearch} />
                    </Field>
                    <Field label="Phone number" htmlFor="sg-phone">
                      <InputWithIcon id="sg-phone" icon={PhoneIcon} placeholder="+1 555 0100" />
                    </Field>
                  </FieldRow>
                  <FieldRow>
                    <Field label="Password" htmlFor="sg-password">
                      <PasswordInput id="sg-password" defaultValue="correct horse" />
                    </Field>
                    <Field label="API key" htmlFor="sg-secret" hint="Write-only: never shown again after saving.">
                      <SecretInput id="sg-secret" value={secret} onChange={(event) => setSecret(event.target.value)} saved />
                    </Field>
                  </FieldRow>
                  <Field label="Invite by email" htmlFor="sg-chips" hint="Press Enter, comma or semicolon, or paste a list.">
                    <ChipInput id="sg-chips" aria-label="Invite by email" itemLabel="email" values={emails} onValuesChange={setEmails} validate={validateEmail} />
                  </Field>
                  <Field label="Knowledge file" htmlFor="sg-file">
                    <FileInput id="sg-file" />
                  </Field>
                  <FormError>We couldn&rsquo;t save the agent. Check the highlighted fields and try again.</FormError>
                </CardContent>
              </Card>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="flex flex-col gap-3">
                  <CheckboxRow label="Record calls" description="Recordings are kept for 30 days." defaultChecked />
                  <RadioRow name="sg-radio" label="Answer every call" defaultChecked />
                  <RadioRow name="sg-radio" label="Answer during business hours" />
                  <label className="flex items-center gap-2 text-control">
                    <Checkbox defaultChecked /> Radix checkbox
                  </label>
                  <SwitchRow label="Let callers interrupt" description="The agent stops talking when the caller speaks." checked={switchOn} onCheckedChange={setSwitchOn} />
                  <div className="flex items-center gap-3">
                    <Switch aria-label="Small switch" size="sm" defaultChecked />
                    <Switch aria-label="Disabled switch" disabled />
                  </div>
                </div>
                <div className="flex flex-col gap-2">
                  <OptionCard name="sg-mode" value="voice" title="Voice" description="Callers talk to the agent on the phone or web." defaultChecked />
                  <OptionCard name="sg-mode" value="text" title="Text" description="Callers type, and the agent answers in text." />
                  <OptionCard name="sg-mode" value="video" title="Video" description="Voice plus a camera or screen share." aside={<Badge tone="warning">Beta</Badge>} />
                </div>
              </div>
            </Specimen>

            <Specimen id="select" title="Select and combobox" description="One custom select replaces every native one. Long lists use the searchable combobox.">
              <FieldRow>
                <Field label="Trunk" htmlFor="sg-trunk">
                  <SimpleSelect
                    id="sg-trunk"
                    value={trunk}
                    onValueChange={setTrunk}
                    placeholder="Choose a trunk"
                    options={[
                      { value: "", label: "No trunk" },
                      { value: "main", label: "Main line", group: "Inbound" },
                      { value: "overflow", label: "Overflow", group: "Inbound" },
                      { value: "outbound", label: "Outbound", group: "Outbound" },
                    ]}
                  />
                </Field>
                <Field label="Model" htmlFor="sg-model" hint="Type to narrow 140 models, or use your own model id.">
                  <Combobox id="sg-model" aria-label="Model" groups={MODEL_GROUPS} value={model} onValueChange={setModel} maxRows={50} allowCustom />
                </Field>
              </FieldRow>
            </Specimen>

            <Specimen id="navigation" title="Segmented control and tabs" description="Segmented controls filter or switch modes. Tabs split one object into sections.">
              <SegmentedControl
                label="Filter sessions"
                value={filter}
                onValueChange={setFilter}
                options={[
                  { value: "all", label: "All", count: 128 },
                  { value: "live", label: "Live", count: 3 },
                  { value: "failed", label: "Failed", count: 4 },
                  { value: "ended", label: "Ended", count: 121 },
                ]}
              />
              <Tabs defaultValue="overview">
                <TabsList>
                  <TabsTrigger value="overview">Overview</TabsTrigger>
                  <TabsTrigger value="transcript">
                    Transcript <TabsCount>42</TabsCount>
                  </TabsTrigger>
                  <TabsTrigger value="costs">Costs</TabsTrigger>
                </TabsList>
                <TabsContent value="overview" className="text-label text-text-secondary">
                  The overview tab.
                </TabsContent>
                <TabsContent value="transcript" className="text-label text-text-secondary">
                  The transcript tab.
                </TabsContent>
                <TabsContent value="costs" className="text-label text-text-secondary">
                  The costs tab.
                </TabsContent>
              </Tabs>
              <Row label="Theme switcher (account menu) and keyboard hint">
                <ThemeSwitcher value={themeChoice} onValueChange={setThemeChoice} />
                <span className="flex items-center gap-1 text-label text-text-secondary">
                  Press <Kbd>⌘</Kbd>
                  <Kbd>K</Kbd> to search
                </span>
              </Row>
            </Specimen>

            <Specimen id="alerts" title="Alerts" description="Danger alerts are announced assertively, and everything else politely.">
              <div className="flex flex-col gap-2.5">
                {ALERT_TONES.map((tone) => (
                  <Alert key={tone} tone={tone} title={tone === "danger" ? "Couldn't reach the server" : undefined}>
                    {tone === "danger"
                      ? "Check your connection and try again."
                      : `A ${tone} alert explains what happened and what to do next.`}
                  </Alert>
                ))}
                <ErrorBanner error={new TypeError("Failed to fetch")} context={{ action: "load agents" }} onRetry={() => toast.info("Retrying.")} />
                <ReadOnlyNote variant="block">You can view connections. Ask an admin to add or change them.</ReadOnlyNote>
              </div>
            </Specimen>

            <Specimen id="data" title="Stats, cards, list cards and tables">
              <StatGrid>
                <StatCard label="Agents" value="12" icon={AGENTS_ICON} hint="3 published this week" />
                <StatCard label="Live now" value="3" icon={PhoneIcon} hint="Across 2 connections" />
                <StatCard label="Sessions, 7 days" value="1,204" icon={ClockIcon} hint="Up 12 % on last week" />
                <StatCard label="Spend, 7 days" value="$48.20" icon={CoinsIcon} hint="Estimate" />
              </StatGrid>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                <Card>
                  <CardHeader>
                    <CardTitle>Details</CardTitle>
                    <CardDescription>What this agent runs on.</CardDescription>
                    <CardAction>
                      <Button size="sm">Edit</Button>
                    </CardAction>
                  </CardHeader>
                  <CardContent className="flex flex-col gap-4">
                    <MetaList
                      items={[
                        { term: "Created", value: "12 Sep, 09:14" },
                        { term: "Connection", value: "Production" },
                        { term: "Turns, median", value: "14" },
                      ]}
                    />
                    <CardInset>Inset panels hold secondary content inside a card.</CardInset>
                  </CardContent>
                  <CardFooter>
                    <Button>Cancel</Button>
                    <Button variant="primary">Save changes</Button>
                  </CardFooter>
                </Card>
                <ListCard label="Recent sessions">
                  <ListCardRow
                    leading={<Avatar name="Ada Lovelace" />}
                    title="Ada Lovelace"
                    meta="Front desk · 2 min ago"
                    trailing={<StatusPill tone="live">Live</StatusPill>}
                    href="#data"
                  />
                  <ListCardRow
                    leading={<Avatar name="Grace Hopper" />}
                    title="Grace Hopper"
                    meta="Claims · 09:42"
                    trailing={<StatusPill tone="success">Done</StatusPill>}
                    href="#data"
                  />
                  <ListCardRow
                    leading={<Avatar name="Team inbox" size="md" />}
                    title="Team inbox"
                    meta="4 members"
                    trailing={<RowMenu label="More actions for Team inbox" actions={[{ label: "Rename", icon: PencilIcon, onSelect: () => {} }]} />}
                  />
                </ListCard>
              </div>
              <Table framed aria-label="Agent costs">
                <TableHeader>
                  <TableRow>
                    <TableHead>Agent</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead numeric>Sessions</TableHead>
                    <TableHead numeric>Cost</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {[
                    ["Front desk", "published", "812", "$31.04"],
                    ["Claims intake", "draft", "0", "$0.00"],
                    ["Renewals", "archived", "392", "$17.16"],
                  ].map(([name, state, sessions, cost]) => (
                    <TableRow key={name}>
                      <TableCell className="font-medium">{name}</TableCell>
                      <TableCell>
                        <LifecycleBadge state={state} size="sm" />
                      </TableCell>
                      <TableCell numeric>{sessions}</TableCell>
                      <TableCell numeric>{cost}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Specimen>

            <Specimen id="states" title="Empty, no matches, loading and progress" description="Every screen designs each state, so nothing is ever blank.">
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <EmptyState
                  icon={UsersIcon}
                  title="No teammates yet"
                  description="Invite people to build and review agents with you."
                  action={
                    <Button variant="primary">
                      <PlusIcon /> Invite people
                    </Button>
                  }
                />
                <NoMatches items="agents" query="renewal" onClear={() => toast.info("Filters cleared.")} />
              </div>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <Card>
                  <CardContent className="pt-5">
                    <LoadingRegion label="Loading agent details">
                      <div className="flex items-center gap-3">
                        <Skeleton className="size-10 rounded-pill" />
                        <div className="flex-1">
                          <SkeletonText lines={2} />
                        </div>
                      </div>
                      <SkeletonText lines={3} className="mt-4" />
                    </LoadingRegion>
                    <LoadingRow label="Loading documents…" className="mt-4" />
                  </CardContent>
                </Card>
                <Card>
                  <CardContent className="pt-5">
                    <ProgressSteps
                      label="Indexing progress"
                      steps={[
                        { id: "read", label: "Reading files", status: "done", detail: "12 of 12 files" },
                        { id: "chunk", label: "Splitting into passages", status: "done" },
                        { id: "embed", label: "Embedding passages", status: "current", detail: "210 of 480" },
                        { id: "publish", label: "Making it searchable", status: "upcoming" },
                      ]}
                    />
                  </CardContent>
                </Card>
              </div>
            </Specimen>

            <Specimen id="overlays" title="Dialogs, menus, popovers, tooltips and toasts" description="Dialogs only. There are no side drawers. Destructive steps use an alert dialog.">
              <Row>
                <Dialog>
                  <DialogTrigger asChild>
                    <Button>Open dialog</Button>
                  </DialogTrigger>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>Rename agent</DialogTitle>
                      <DialogDescription>Callers hear the new name on their next call.</DialogDescription>
                    </DialogHeader>
                    <Field label="Name" htmlFor="sg-dialog-name">
                      <Input id="sg-dialog-name" defaultValue="Front desk" />
                    </Field>
                    <DialogFooter>
                      <DialogClose asChild>
                        <Button>Cancel</Button>
                      </DialogClose>
                      <DialogClose asChild>
                        <Button variant="primary">Rename</Button>
                      </DialogClose>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>
                <Dialog>
                  <DialogTrigger asChild>
                    <Button>Open large panel</Button>
                  </DialogTrigger>
                  <DialogContent size="lg">
                    <DialogHeader>
                      <DialogTitle>Version history</DialogTitle>
                      <DialogDescription>Every save is a version you can restore.</DialogDescription>
                    </DialogHeader>
                    <DialogBody>
                      {Array.from({ length: 24 }, (_, i) => (
                        <p key={i} className="text-label text-text-secondary">
                          Version {24 - i} · saved by Ada · {i + 1} h ago
                        </p>
                      ))}
                    </DialogBody>
                    <DialogFooter>
                      <DialogClose asChild>
                        <Button>Close</Button>
                      </DialogClose>
                      <DialogClose asChild>
                        <Button variant="primary">Restore</Button>
                      </DialogClose>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>
                <ConfirmDialog
                  trigger={<Button variant="danger-outline">Delete agent</Button>}
                  title="Delete “Front desk”?"
                  description="The agent and its versions are removed. Past sessions stay."
                  confirmLabel="Delete agent"
                  onConfirm={() => new Promise((resolve) => setTimeout(resolve, 900))}
                />
                <TypedConfirmDialog
                  trigger={<Button variant="danger-outline">Delete workspace</Button>}
                  title="Delete this workspace?"
                  description="This can't be undone."
                  confirmText="DELETE"
                  confirmLabel="Delete workspace"
                  onConfirm={() => new Promise((resolve) => setTimeout(resolve, 900))}
                >
                  <ul className="list-inside list-disc">
                    <li>12 agents and their versions</li>
                    <li>4 knowledge bases</li>
                  </ul>
                </TypedConfirmDialog>
              </Row>
              <Row>
                <Popover>
                  <PopoverTrigger asChild>
                    <Button>Open popover</Button>
                  </PopoverTrigger>
                  <PopoverContent>
                    <PopoverHeader>
                      <PopoverTitle>Connection</PopoverTitle>
                      <PopoverDescription>Production · LiveKit Cloud</PopoverDescription>
                    </PopoverHeader>
                    <Button variant="link" className="self-start px-2 pb-1.5">
                      Open connection <ArrowRightIcon />
                    </Button>
                  </PopoverContent>
                </Popover>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button>
                      <KeyRoundIcon /> Hover for a tooltip
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Supplementary detail only, never essential.</TooltipContent>
                </Tooltip>
              </Row>
              <Row label="Toasts (success and info leave after 6 s, and errors and warnings stay)">
                <Button onClick={() => toast.success("Agent saved.")}>Success</Button>
                <Button onClick={() => toast.info("A new version is available.")}>Info</Button>
                <Button onClick={() => toast.warning("Your key expires in 3 days.")}>Warning</Button>
                <Button onClick={() => toast.error("Couldn't save the agent. Try again in a moment.")}>Error</Button>
              </Row>
            </Specimen>

            <Specimen
              id="caller-page"
              title="Caller page exception (D3)"
              description="The public session page (/s/[slug] and its embed) uses these tokens, icons, primitives, states and copy, with three documented exceptions."
            >
              <Card>
                <CardContent className="flex flex-col gap-4 md:flex-row md:items-start">
                  <ul className="flex max-w-[52ch] list-disc flex-col gap-1.5 pl-5 text-body text-text-secondary">
                    <li>
                      <span className="font-medium text-foreground">Always dark.</span> It feels like a phone call and
                      ignores the theme setting. The preview theme above does not change it.
                    </li>
                    <li>
                      <span className="font-medium text-foreground">16 px body text,</span> not 14 px. Callers read it at
                      arm&rsquo;s length, often on a phone.
                    </li>
                    <li>
                      <span className="font-medium text-foreground">A bottom sheet</span> for the transcript on phones:
                      the one sheet in the product. Everywhere else uses dialogs.
                    </li>
                  </ul>
                  {/* A static picture of the phone layout, inside a `.dark` subtree so every token re-resolves. */}
                  <div
                    aria-hidden="true"
                    data-slot="caller-page-specimen"
                    className="dark relative mx-auto flex h-[320px] w-[240px] max-w-full shrink-0 flex-col overflow-hidden rounded-lg border border-border bg-background text-base text-foreground"
                  >
                    <div className="flex items-center justify-between px-3 py-2">
                      <span className="font-medium">Ada</span>
                      <StatusPill tone="live" size="sm">
                        Live
                      </StatusPill>
                    </div>
                    <div className="flex flex-1 items-center justify-center bg-stage text-text-secondary">Listening…</div>
                    <div className="rounded-t-lg border-t border-border bg-card px-3 pt-2 pb-3 shadow-overlay">
                      <div className="mx-auto mb-2 h-1 w-9 rounded-pill bg-muted-strong" />
                      <p className="font-medium">Transcript</p>
                      <p className="text-text-secondary">Hi, how can I help today?</p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </Specimen>
          </div>
        </Page>
        <Toaster />
      </div>
    </TooltipProvider>
  );
}

import * as React from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ClockIcon } from "lucide-react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { IfCan, readOnlyCopy } from "@/components/console/shared/permission";
import { Avatar, MetaList, StatCard, StatGrid, initials } from "@/components/shared/data-display";
import { ListCard, ListCardRow } from "@/components/shared/list-card";
import { NewResourceButton } from "@/components/shared/new-resource-button";
import { Page, PageHeader } from "@/components/shared/page-header";
import { ReadOnlyNote } from "@/components/shared/read-only-note";
import { SegmentedControl } from "@/components/shared/segmented-control";
import { ThemeSwitcher } from "@/components/shared/theme-switcher";
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsCount, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Card, table and list card (spec 6.7)", () => {
  it("card has a hairline, no shadow, a divided header and a muted footer", () => {
    const { container } = render(
      <Card>
        <CardHeader>
          <CardTitle>Details</CardTitle>
        </CardHeader>
        <CardContent>Body</CardContent>
        <CardFooter>Footer</CardFooter>
      </Card>,
    );
    const card = container.querySelector('[data-slot="card"]');
    expect(card?.className).toContain("border-border");
    expect(card?.className).not.toMatch(/shadow|ring-/);
    expect(container.querySelector('[data-slot="card-header"]')?.className).toContain("not-last:border-b");
    expect(container.querySelector('[data-slot="card-footer"]')?.className).toContain("bg-muted");
  });

  it("table heads are 36 px muted, numeric cells align right with tabular figures, framed adds the wrapper", () => {
    const { container } = render(
      <Table framed aria-label="Costs">
        <TableHeader>
          <TableRow>
            <TableHead>Agent</TableHead>
            <TableHead numeric>Cost</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>Front desk</TableCell>
            <TableCell numeric>1.20</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );
    expect(screen.getAllByRole("columnheader")[0].className).toContain("h-9");
    expect(screen.getAllByRole("columnheader")[1].className).toContain("text-right");
    expect(screen.getByText("1.20").className).toContain("tabular-nums");
    expect(container.querySelector('[data-slot="table-container"]')?.hasAttribute("data-framed")).toBe(true);
  });

  it("list card rows link with a trailing arrow", () => {
    render(
      <ListCard label="Recent sessions">
        <ListCardRow title="Front desk" meta="2 min ago" href="/console/sessions/s1" />
        <ListCardRow title="Claims" meta="Yesterday" />
      </ListCard>,
    );
    expect(screen.getByRole("list", { name: "Recent sessions" })).toBeTruthy();
    const link = screen.getByRole("link", { name: /Front desk/ });
    expect(link.getAttribute("href")).toBe("/console/sessions/s1");
    expect(link.querySelector(".lucide-arrow-right")).toBeTruthy();
  });
});

describe("Meta list, stats and avatar (spec 6.7)", () => {
  it("renders term/value pairs, stat cards and a stat grid", () => {
    const { container } = render(
      <>
        <MetaList items={[{ term: "Created", value: "12 Sep" }]} />
        <StatGrid>
          <StatCard label="Sessions, 7 days" value="1,204" icon={ClockIcon} hint="Up 12 %" />
        </StatGrid>
      </>,
    );
    expect(container.querySelector("dt")?.textContent).toBe("Created");
    expect(container.querySelector("dd")?.className).toContain("tabular-nums");
    expect(screen.getByText("1,204").className).toContain("text-stat");
    expect(container.querySelector('[data-slot="stat-grid"]')?.className).toContain("minmax(180px,1fr)");
  });

  it("avatar uses first and last initials and falls back when the photo fails", () => {
    expect(initials("Ada Byron Lovelace")).toBe("AL");
    expect(initials("ada@example.com")).toBe("A");
    render(<Avatar name="Ada Lovelace" src="https://example.test/a.png" />);
    const avatar = screen.getByRole("img", { name: "Ada Lovelace" });
    fireEvent.error(avatar.querySelector("img") as HTMLImageElement);
    expect(avatar.textContent).toBe("AL");
  });
});

describe("Segmented control, tabs and theme switcher (spec 6.7)", () => {
  it("segmented control is a radio group with counts", () => {
    function Harness() {
      const [value, setValue] = React.useState("all");
      return (
        <SegmentedControl
          label="Filter by status"
          value={value}
          onValueChange={setValue}
          options={[
            { value: "all", label: "All", count: 12 },
            { value: "failed", label: "Failed", count: 2 },
          ]}
        />
      );
    }
    render(<Harness />);
    expect(screen.getByRole("radiogroup", { name: "Filter by status" })).toBeTruthy();
    // The count keeps its own words in the name, never "Failed2".
    const failed = screen.getByRole("radio", { name: "Failed (2)" });
    expect(screen.getByRole("radio", { name: "All (12)" })).toBeTruthy();
    fireEvent.click(failed);
    expect(failed.getAttribute("aria-checked")).toBe("true");
  });

  it("tabs draw the accent underline and a count pill", () => {
    render(
      <Tabs defaultValue="a">
        <TabsList>
          <TabsTrigger value="a">
            Overview <TabsCount>3</TabsCount>
          </TabsTrigger>
          <TabsTrigger value="b">Costs</TabsTrigger>
        </TabsList>
        <TabsContent value="a">A</TabsContent>
      </Tabs>,
    );
    const tab = screen.getByRole("tab", { name: "Overview 3" });
    expect(tab.getAttribute("aria-selected")).toBe("true");
    expect(tab.className).toContain("after:bg-brand");
  });

  it("theme switcher offers System, Light and Dark in controlled mode", () => {
    const onValueChange = vi.fn();
    render(<ThemeSwitcher value="system" onValueChange={onValueChange} />);
    expect(screen.getByRole("radiogroup", { name: "Theme" })).toBeTruthy();
    expect(screen.getAllByRole("radio").map((r) => r.getAttribute("aria-label"))).toEqual(["System", "Light", "Dark"]);
    fireEvent.click(screen.getByRole("radio", { name: "Dark" }));
    expect(onValueChange).toHaveBeenCalledWith("dark");
  });
});

describe("Page template (spec 7.3)", () => {
  it("page header renders a named back link, eyebrow, h1 with badge, intro and actions", () => {
    render(
      <Page width="wide">
        <PageHeader
          back={{ href: "/console/agents", label: "Back to agents" }}
          eyebrow="Agent"
          title="Front desk"
          badge={<span>Live</span>}
          description="Answers the main line."
          actions={<button type="button">Publish</button>}
        />
      </Page>,
    );
    expect(screen.getByRole("link", { name: "Back to agents" }).getAttribute("href")).toBe("/console/agents");
    expect(screen.getByRole("heading", { level: 1, name: "Front desk" })).toBeTruthy();
    expect(screen.getByText("Agent").className).toContain("uppercase");
    expect(screen.getByText("Live")).toBeTruthy();
    expect(document.querySelector('[data-slot="page"]')?.className).toContain("max-w-[1440px]");
  });
});

describe("Permission pattern (spec 8.5, D12)", () => {
  function stubRole(role: "viewer" | "builder" | "admin") {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("auth/me")) {
          return {
            ok: true,
            status: 200,
            json: async () => ({ user: { id: "u1", email: "a@b.test" }, workspaces: [{ id: "w1", name: "W", slug: "w", role }] }),
          } as Response;
        }
        return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) } as Response;
      }),
    );
  }

  function withClient(ui: React.ReactElement) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
  }

  it("hides row actions below the floor and shows the fallback", async () => {
    stubRole("viewer");
    withClient(
      <IfCan min="builder" fallback={<ReadOnlyNote>{readOnlyCopy("builder", "edit agents")}</ReadOnlyNote>}>
        <button type="button">Edit</button>
      </IfCan>,
    );
    expect(await screen.findByText("Ask a builder or admin to edit agents.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  it("renders the action at or above the floor", async () => {
    stubRole("admin");
    withClient(
      <IfCan min="admin">
        <button type="button">Edit</button>
      </IfCan>,
    );
    expect(await screen.findByRole("button", { name: "Edit" })).toBeTruthy();
  });

  it("New … becomes a read-only note for a viewer and a primary button for a builder", async () => {
    stubRole("viewer");
    const { unmount } = withClient(
      <NewResourceButton href="/console/connections/new" min="admin" readOnlyNote="Ask an admin to add connections.">
        New connection
      </NewResourceButton>,
    );
    expect(await screen.findByText("Ask an admin to add connections.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "New connection" })).toBeNull();
    expect(screen.queryByRole("link", { name: "New connection" })).toBeNull();
    unmount();

    stubRole("builder");
    withClient(<NewResourceButton href="/console/agents/new">New agent</NewResourceButton>);
    await waitFor(() => expect(screen.getByRole("link", { name: "New agent" }).getAttribute("data-variant")).toBe("primary"));
  });
});

"use client";

import * as React from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { FormProvider, useForm, type FieldErrors } from "react-hook-form";
import { toast } from "sonner";

import { Skeleton } from "@/components/ui/skeleton";
import { useAgent, useDeleteAgent, useUpdateAgent, useValidateAgent } from "@/components/console/lib/api-hooks";
import { firstErrorMessage } from "@/components/console/lib/form-errors";
import { agentEditorFormSchema, type AgentEditorForm } from "@/components/console/lib/schemas";
import { zodResolver } from "@/components/console/lib/zod-resolver";
import { ErrorBanner, errorMessage } from "@/components/console/shared/error-banner";
import { useSetBreadcrumbs } from "@/components/console/shell/breadcrumb-context";
import type { AgentOut, ValidationResult } from "@/contracts/lkap-contracts";
import { pluralize } from "@/lib/format";

import { EditorContextProvider, type EditorContextValue } from "./editor/editor-context";
import { EditorShell } from "./editor/editor-shell";
import { buildAgentUpdate, toFormValues, unappliedFields } from "./editor/form-values";
import type { SaveOutcome } from "./editor/publish-popover";
import { visibleSections } from "./editor/registry";
import { DEFAULT_SECTION_ID, EDITOR_SECTIONS, EDITOR_SLOTS } from "./editor/sections";
import type { EditorSectionDef } from "./editor/types";
import { UnsavedGuard } from "./editor/unsaved-guard";
import {
  firstSectionWithIssues,
  formPathForIssue,
  GENERAL_SECTION,
  issuesFromApiError,
  issuesFromFieldErrors,
  issuesFromValidation,
  summarizeIssues,
  validationMessages,
  type EditorIssue,
} from "./editor/validation-map";

/** How long the quiet "Configuration looks good" line stays (docs/UI_UX_SPEC.md §6). */
const LOOKS_GOOD_MS = 6000;

export interface AgentEditorProps {
  agentId: string;
  /** Section list override (tests); defaults to the registry (`editor/sections.ts`). */
  sections?: EditorSectionDef[];
}

export function AgentEditor({ agentId, sections = EDITOR_SECTIONS }: AgentEditorProps) {
  const { data: agent, isLoading, isError, error, refetch } = useAgent(agentId);
  // Top bar trail "Agents / <name>" (docs/UI_UX_SPEC.md §3.2); a no-op outside the console shell.
  useSetBreadcrumbs([{ label: "Agents", href: "/console/agents" }, { label: agent?.name ?? "Agent" }]);

  if (isLoading) return <EditorSkeleton />;

  if (isError || !agent) {
    return <ErrorBanner message={`Couldn't load this agent — ${errorMessage(error)}`} onRetry={() => refetch()} />;
  }

  return <AgentEditorFormBody agent={agent} allSections={sections} />;
}

function EditorSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading agent" className="flex flex-col gap-6">
      <div className="flex flex-col gap-2 border-b border-border pb-4">
        <Skeleton className="h-3 w-16" />
        <Skeleton className="h-7 w-64" />
        <Skeleton className="h-5 w-80" />
      </div>
      <div className="grid gap-6 lg:grid-cols-[200px_minmax(0,1fr)_280px]">
        <Skeleton className="hidden h-72 lg:block" />
        <Skeleton className="h-96" />
        <Skeleton className="hidden h-96 lg:block" />
      </div>
    </div>
  );
}

function focusFieldFor(issue: EditorIssue, setFocus: (path: string) => void): boolean {
  const formPath = formPathForIssue(issue.path);
  if (!formPath || !issue.path) return false;
  const before = document.activeElement;
  try {
    setFocus(formPath);
  } catch {
    // Not a registered field (a Controller without a ref); fall back to the DOM.
  }
  if (document.activeElement !== before && document.activeElement !== document.body) return true;
  const selectors = [
    `[data-issue-path="${issue.path}"]`,
    `[name="${formPath}"]`,
    `[data-issue-path^="${issue.path}."]`,
    `[name^="${formPath}."]`,
  ];
  for (const selector of selectors) {
    const node = document.querySelector<HTMLElement>(selector);
    if (node && !node.hasAttribute("disabled")) {
      node.focus();
      node.scrollIntoView?.({ block: "center" });
      return true;
    }
  }
  return false;
}

function AgentEditorFormBody({ agent, allSections }: { agent: AgentOut; allSections: EditorSectionDef[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const updateAgent = useUpdateAgent(agent.id);
  const validateAgent = useValidateAgent(agent.id);
  const deleteAgent = useDeleteAgent();
  const contentRef = React.useRef<HTMLDivElement>(null);

  const form = useForm<AgentEditorForm>({
    resolver: zodResolver(agentEditorFormSchema),
    defaultValues: toFormValues(agent),
  });

  React.useEffect(() => {
    form.reset(toFormValues(agent));
    // Reset only when a different agent or a new config version arrives, not on every refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id, agent.config_version]);

  // ---- sections and ?section= ----
  const mode = form.watch("mode");
  const sections = React.useMemo(() => visibleSections(allSections, { agent, mode }), [allSections, agent, mode]);
  const sectionParam = searchParams.get("section");
  const [requested, setRequested] = React.useState<string>(sectionParam ?? DEFAULT_SECTION_ID);
  const lastParam = React.useRef(sectionParam);
  React.useEffect(() => {
    // Back/forward (or a link) changed ?section=; follow it.
    if (sectionParam !== lastParam.current) {
      lastParam.current = sectionParam;
      if (sectionParam) setRequested(sectionParam);
    }
  }, [sectionParam]);
  const active =
    sections.find((section) => section.id === requested) ??
    sections.find((section) => section.id === DEFAULT_SECTION_ID) ??
    sections[0];

  const goToSection = React.useCallback(
    (id: string) => {
      setRequested(id);
      const params = new URLSearchParams(searchParams.toString());
      params.set("section", id);
      lastParam.current = id;
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
      const top = contentRef.current?.getBoundingClientRect().top;
      if (top !== undefined && top < 0) window.scrollTo({ top: 0 });
    },
    [pathname, router, searchParams],
  );

  // ---- issues ----
  const [lastResult, setLastResult] = React.useState<Partial<ValidationResult> | null>(
    null,
  );
  // Client (zod) errors come from the live `formState.errors`, so a fixed field
  // clears its dot as soon as RHF re-validates; server issues come from the
  // last validate call or failed save.
  const computedIssues = [
    ...issuesFromFieldErrors(form.formState.errors, sections),
    ...issuesFromValidation(lastResult, sections),
  ];
  const issuesKey = JSON.stringify(computedIssues);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const issues = React.useMemo(() => computedIssues, [issuesKey]);
  const summary = React.useMemo(() => summarizeIssues(issues), [issues]);
  const sectionOrder = React.useMemo(() => sections.map((section) => section.id), [sections]);

  const focusIssue = React.useCallback(
    (issue: EditorIssue) => {
      if (issue.section !== GENERAL_SECTION) goToSection(issue.section);
      let attempts = 0;
      const tryFocus = () => {
        attempts += 1;
        if (focusFieldFor(issue, (path) => form.setFocus(path as never)) || attempts >= 10) return;
        window.setTimeout(tryFocus, 50);
      };
      window.setTimeout(tryFocus, 0);
    },
    [form, goToSection],
  );

  const goToFirstIssue = React.useCallback(() => {
    const target = firstSectionWithIssues(issues, sectionOrder, "error") ?? firstSectionWithIssues(issues, sectionOrder, "warning");
    if (target) goToSection(target);
  }, [issues, sectionOrder, goToSection]);

  // ---- "Configuration looks good" ----
  const [looksGood, setLooksGood] = React.useState(false);
  React.useEffect(() => {
    if (!looksGood) return;
    const timer = window.setTimeout(() => setLooksGood(false), LOOKS_GOOD_MS);
    return () => window.clearTimeout(timer);
  }, [looksGood]);

  // Validate the saved config once on open so the section dots are meaningful before the first save.
  React.useEffect(() => {
    let cancelled = false;
    validateAgent
      .mutateAsync()
      .then((result) => {
        if (!cancelled) setLastResult(result);
      })
      .catch(() => {
        // Best effort; dots simply stay empty.
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id]);

  // ---- save ----
  const [saving, setSaving] = React.useState(false);

  const persist = React.useCallback(
    async (values: AgentEditorForm): Promise<SaveOutcome | null> => {
      const body = buildAgentUpdate(agent, values);
      setSaving(true);
      setLooksGood(false);
      try {
        const updated = await updateAgent.mutateAsync(body);
        form.reset(toFormValues(updated));
        const unapplied = unappliedFields(body, updated);
        let validation: ValidationResult | null = null;
        try {
          validation = await validateAgent.mutateAsync();
          setLastResult(validation);
        } catch {
          // Validation is best effort; a failed validate call must not hide a successful save.
          setLastResult(null);
        }
        const { errors, warnings } = validationMessages(validation);
        if (errors.length > 0) toast.success(`Saved with ${pluralize(errors.length, "error", "errors")}`);
        else if (warnings.length > 0) toast.success(`Saved with ${pluralize(warnings.length, "warning", "warnings")}`);
        else {
          toast.success("Saved");
          if (validation) setLooksGood(true);
        }
        if (unapplied.length > 0) {
          toast.warning(`Not saved: ${unapplied.join(", ")}`, {
            description: "This version of the API doesn't store these settings yet.",
          });
        }
        return { agent: updated, validation };
      } catch (error) {
        const serverIssues = issuesFromApiError(error, sections);
        if (serverIssues) {
          setLastResult((error as { details?: ValidationResult }).details ?? null);
          const errorCount = serverIssues.filter((issue) => issue.severity === "error").length;
          toast.error(`Couldn't save — fix ${pluralize(errorCount || serverIssues.length, "issue", "issues")} first`);
          const target = firstSectionWithIssues(serverIssues, sectionOrder, "error");
          if (target) goToSection(target);
        } else {
          toast.error(`Couldn't save — ${errorMessage(error)}`);
        }
        return null;
      } finally {
        setSaving(false);
      }
    },
    [agent, form, goToSection, sectionOrder, sections, updateAgent, validateAgent],
  );

  const onInvalid = React.useCallback(
    (errors: FieldErrors<AgentEditorForm>) => {
      const clientIssues = issuesFromFieldErrors(errors, sections);
      toast.error(firstErrorMessage(errors) ?? "Fix the highlighted fields before saving.");
      const first = clientIssues[0];
      if (first) focusIssue(first);
    },
    [focusIssue, sections],
  );

  const saveNow = React.useCallback(
    () =>
      new Promise<SaveOutcome | null>((resolve) => {
        void form.handleSubmit(
          async (values) => resolve(await persist(values)),
          (errors) => {
            onInvalid(errors);
            resolve(null);
          },
        )();
      }),
    [form, onInvalid, persist],
  );

  const dirty = form.formState.isDirty;

  // ⌘S / Ctrl+S saves.
  const saveRef = React.useRef({ dirty, saving, saveNow });
  saveRef.current = { dirty, saving, saveNow };
  React.useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "s") {
        event.preventDefault();
        const current = saveRef.current;
        if (current.dirty && !current.saving) void current.saveNow();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  async function onDelete() {
    try {
      await deleteAgent.mutateAsync(agent.id);
      toast.success("Deleted");
      router.push("/console/agents");
    } catch (error) {
      toast.error(`Couldn't delete — ${errorMessage(error)}`);
      throw error;
    }
  }

  const contextValue = React.useMemo<EditorContextValue>(
    () => ({ agent, sections, activeSection: active?.id ?? DEFAULT_SECTION_ID, goToSection, issues, focusIssue }),
    [agent, sections, active, goToSection, issues, focusIssue],
  );

  if (!active) return null;
  const ActiveComponent = active.Component;

  return (
    <FormProvider {...form}>
      <EditorContextProvider value={contextValue}>
        <form
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            // Enter in a field submits the form; a clean form has nothing to save.
            if (dirty && !saving) void saveNow();
          }}
        >
          <UnsavedGuard dirty={dirty} name={agent.name} />
          <EditorShell
            agent={agent}
            sections={sections}
            active={active}
            onSelectSection={goToSection}
            summary={summary}
            slots={EDITOR_SLOTS}
            dirty={dirty}
            saving={saving}
            looksGood={looksGood}
            saveNow={saveNow}
            onValidated={setLastResult}
            goToFirstIssue={goToFirstIssue}
            onDelete={onDelete}
            contentRef={contentRef}
          >
            <ActiveComponent agent={agent} />
          </EditorShell>
        </form>
      </EditorContextProvider>
    </FormProvider>
  );
}

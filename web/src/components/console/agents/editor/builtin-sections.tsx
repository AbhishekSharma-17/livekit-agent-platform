import {
  AudioLinesIcon,
  BookOpenIcon,
  CircleDotIcon,
  FileTextIcon,
  GaugeIcon,
  PanelRightIcon,
  WorkflowIcon,
  WrenchIcon,
} from "lucide-react";

import { InstructionsTab } from "@/components/console/agents/tabs/instructions-tab";
import { KnowledgeTab } from "@/components/console/agents/tabs/knowledge-tab";
import { PanelTab } from "@/components/console/agents/tabs/panel-tab";
import { ProvidersTab } from "@/components/console/agents/tabs/providers-tab";
import { ToolsSection } from "@/components/console/telephony/tools-section";

import { FlowSection } from "./sections/flow-section";
import { LimitsSection } from "./sections/limits-section";
import { RecordingSection } from "./sections/recording-section";
import type { EditorSectionDef } from "./types";

/**
 * Built-in sections, in the v2 order (UI_UX_SPEC-V2-AMENDMENTS §1):
 * providers · instructions · flow · panel · tools · knowledge · recording · limits.
 * Orders are spaced by 10 so an extension can insert between two built-ins.
 * Keyword heuristics follow §7.14 (tools before knowledge before panel).
 */
export const BUILTIN_SECTIONS: EditorSectionDef[] = [
  {
    id: "providers",
    label: "Providers",
    icon: AudioLinesIcon,
    order: 10,
    Component: ProvidersTab,
    issuePaths: ["pipeline", "connection_id"],
    issueKeywords: /\b(stt|llm|tts|realtime|provider|credential|model|avatar|image)/i,
    issueKeywordPriority: 10,
  },
  {
    id: "instructions",
    label: "Instructions & voice",
    icon: FileTextIcon,
    order: 20,
    Component: InstructionsTab,
    issuePaths: ["instructions", "voice", "timezone", "pack_settings"],
    issueKeywords: /\b(instruction|greeting|language|timezone|interrupt)/i,
    issueKeywordPriority: 20,
  },
  {
    id: "flow",
    label: "Flow",
    icon: WorkflowIcon,
    order: 30,
    Component: FlowSection,
    visible: ({ mode }) => mode === "flow",
    layout: "full",
    issuePaths: ["flow"],
    issueKeywords: /\b(flow|node|edge)\b/i,
    issueKeywordPriority: 80,
  },
  {
    id: "panel",
    label: "Panel & capabilities",
    icon: PanelRightIcon,
    order: 40,
    Component: PanelTab,
    issuePaths: ["panel", "capabilities", "ui_panel_id"],
    issueKeywords: /\b(panel|camera|screen|vision|chat|block)/i,
    issueKeywordPriority: 50,
  },
  {
    id: "tools",
    label: "Tools",
    icon: WrenchIcon,
    order: 50,
    // V2-19 (R-V2-21/25): WP-5's tools tab plus the "Phone calls" card (transfer destinations).
    Component: ToolsSection,
    issuePaths: ["tools", "telephony"],
    issueKeywords: /\b(tool|http|mcp|transfer|dtmf)/i,
    issueKeywordPriority: 30,
  },
  {
    id: "knowledge",
    label: "Knowledge",
    icon: BookOpenIcon,
    order: 60,
    Component: KnowledgeTab,
    issuePaths: ["knowledge"],
    issueKeywords: /\b(knowledge|kb|top_k)/i,
    issueKeywordPriority: 40,
  },
  {
    id: "recording",
    label: "Recording",
    icon: CircleDotIcon,
    order: 70,
    Component: RecordingSection,
    issuePaths: ["recording"],
    issueKeywords: /\b(recording|egress|storage|retention)/i,
    issueKeywordPriority: 60,
  },
  {
    id: "limits",
    label: "Limits",
    icon: GaugeIcon,
    order: 80,
    Component: LimitsSection,
    issuePaths: ["limits", "allowed_origins"],
    issueKeywords: /\b(limit|concurren|rate|origin)/i,
    issueKeywordPriority: 70,
  },
];

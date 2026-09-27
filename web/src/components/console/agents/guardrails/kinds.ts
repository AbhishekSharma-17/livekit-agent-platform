/**
 * Plain-wording lookups shared by `rule-dialog.tsx` and `rule-list.tsx`
 * (V5-41, §0.1: never "regex" / "classifier" / "moderation" in a heading).
 */
import type { GuardrailRuleForm, MODERATION_CATEGORY_VALUES } from "@/components/console/lib/schemas";

export const RULE_KIND_LABEL: Record<GuardrailRuleForm["kind"], string> = {
  regex: "Pattern",
  classifier: "Instruction",
  provider: "Moderation service",
};

/** `ModerationCategory` → a plain-language label (no OpenAI category slugs in the UI). */
export const CATEGORY_LABELS: Record<(typeof MODERATION_CATEGORY_VALUES)[number], string> = {
  harassment: "Harassment",
  "harassment/threatening": "Threats of harm",
  hate: "Hateful content",
  "hate/threatening": "Hateful threats",
  illicit: "Illegal activity",
  "illicit/violent": "Illegal activity involving violence",
  "self-harm": "Self-harm",
  "self-harm/intent": "Intent to self-harm",
  "self-harm/instructions": "Instructions for self-harm",
  sexual: "Sexual content",
  "sexual/minors": "Sexual content involving minors",
  violence: "Violence",
  "violence/graphic": "Graphic violence",
};

/** One line describing a rule's own check, for the row (never the raw pattern/prompt verbatim if very long). */
export function ruleSummary(rule: GuardrailRuleForm): string {
  switch (rule.kind) {
    case "regex":
      return rule.pattern;
    case "classifier":
      return rule.prompt;
    case "provider":
      return rule.categories.length > 0
        ? rule.categories.map((category) => CATEGORY_LABELS[category]).join(", ")
        : "Any flagged category";
  }
}

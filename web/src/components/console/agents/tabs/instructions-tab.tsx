"use client";

import * as React from "react";
import { Controller, useFormContext } from "react-hook-form";

import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AgentEditorForm } from "@/components/console/lib/schemas";

const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "es", label: "Spanish" },
  { value: "fr", label: "French" },
  { value: "de", label: "German" },
  { value: "hi", label: "Hindi" },
  { value: "ja", label: "Japanese" },
];

export function InstructionsTab() {
  const { register, control, formState } = useFormContext<AgentEditorForm>();
  const errors = formState.errors;

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-border bg-card p-4">
        <label htmlFor="instructions" className="mb-1 block text-sm font-medium">
          Instructions<span className="ml-0.5 text-destructive">*</span>
        </label>
        <Textarea
          id="instructions"
          rows={10}
          className="min-h-48 font-mono text-sm"
          placeholder="You are a helpful assistant for..."
          {...register("config.instructions")}
        />
        {errors.config?.instructions ? (
          <p className="mt-1 text-xs text-destructive">{errors.config.instructions.message}</p>
        ) : null}
      </div>

      <div className="grid gap-4 rounded-xl border border-border bg-card p-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label htmlFor="greeting" className="mb-1 block text-sm font-medium">
            Greeting
          </label>
          <Textarea id="greeting" rows={2} {...register("config.voice.greeting")} />
        </div>

        <div>
          <label htmlFor="greeting-mode" className="mb-1 block text-sm font-medium">
            Greeting mode
          </label>
          <Controller
            control={control}
            name="config.voice.greeting_mode"
            render={({ field }) => (
              <Select value={field.value} onValueChange={field.onChange}>
                <SelectTrigger id="greeting-mode" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="say">Say (TTS reads it verbatim)</SelectItem>
                  <SelectItem value="generate">Generate (model paraphrases it)</SelectItem>
                </SelectContent>
              </Select>
            )}
          />
        </div>

        <div>
          <label htmlFor="voice-language" className="mb-1 block text-sm font-medium">
            Language
          </label>
          <Controller
            control={control}
            name="config.voice.language"
            render={({ field }) => (
              <Select value={field.value} onValueChange={field.onChange}>
                <SelectTrigger id="voice-language" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {LANGUAGES.map((l) => (
                    <SelectItem key={l.value} value={l.value}>
                      {l.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          />
        </div>

        <div>
          <label htmlFor="agent-timezone" className="mb-1 block text-sm font-medium">
            Timezone
          </label>
          <Input id="agent-timezone" {...register("config.timezone")} placeholder="UTC" />
        </div>

        <div>
          <label htmlFor="user-away-timeout" className="mb-1 block text-sm font-medium">
            User-away timeout (seconds)
          </label>
          <Controller
            control={control}
            name="config.voice.user_away_timeout_s"
            render={({ field }) => (
              <Input
                id="user-away-timeout"
                type="number"
                value={field.value ?? ""}
                onChange={(event) => field.onChange(event.target.value === "" ? null : Number(event.target.value))}
              />
            )}
          />
        </div>

        <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
          <span className="text-sm font-medium">Allow interruptions</span>
          <Controller
            control={control}
            name="config.voice.allow_interruptions"
            render={({ field }) => <Switch checked={field.value} onCheckedChange={field.onChange} />}
          />
        </div>
      </div>
    </div>
  );
}

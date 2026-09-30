"use client";

import * as React from "react";

import { SimpleSelect } from "@/components/ui/select";

/**
 * A scene-parameter picker for the preview top bar: the custom Select (no
 * native `<select>`, docs/ui/DESIGN-SYSTEM.md section 6.3). Inside the
 * top bar's GET form, Radix renders a hidden form control under `name`, so
 * "Go" still submits the choice as a query parameter.
 */
export function PreviewSelect({
  name,
  label,
  defaultValue,
  values,
}: {
  name: string;
  label: string;
  defaultValue: string;
  values: readonly string[];
}) {
  const [value, setValue] = React.useState(defaultValue);
  return (
    <SimpleSelect
      name={name}
      aria-label={label}
      size="sm"
      value={value}
      onValueChange={setValue}
      options={values.map((option) => ({ value: option, label: option }))}
      className="w-auto min-w-24"
    />
  );
}

import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

// User UI rule: no side drawers anywhere in the web app — slide-over panels
// are modals (`@/components/ui/dialog`, `DialogContent size=…`). The sheet
// primitive was deleted; this keeps it (and drawer libraries) from coming back
// through `shadcn add sheet`/`drawer` or a new dependency.
const NO_DRAWERS = "Use Dialog — side drawers are not allowed (user UI rule)";

const eslintConfig = [
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [{ name: "vaul", message: NO_DRAWERS }],
          patterns: [
            {
              group: [
                "**/ui/sheet",
                "**/ui/sheet.*",
                "./sheet",
                "./sheet.*",
                "**/ui/drawer",
                "**/ui/drawer.*",
                "./drawer",
                "./drawer.*",
                "vaul/*",
              ],
              message: NO_DRAWERS,
            },
          ],
        },
      ],
    },
  },
  {
    ignores: [
      "node_modules/**",
      ".next/**",
      "out/**",
      "build/**",
      "next-env.d.ts",
    ],
  },
];

export default eslintConfig;

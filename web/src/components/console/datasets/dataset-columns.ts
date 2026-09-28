/**
 * Client-side column sniffing for the upload dialog (V6-19, D-V6-27): the api decides a
 * dataset's real columns once it parses the file (`lkap_api.datasets.parse.parse_dataset`),
 * but the upload dialog needs to show a column list — with a type and a "match on this"
 * checkbox per column — *before* that upload happens, so the admin can build `key_columns`.
 * This is a light, best-effort mirror of the same idea, not the source of truth: the upload
 * itself (`POST /v1/datasets`) still validates for real, and a file this can't make sense of
 * (an unusual quoting scheme, say) just shows no columns rather than a wrong guess.
 */

/** `lkap_api.datasets.parse.dataset_format`'s console mirror — by extension only. */
export function datasetFormatFromFilename(filename: string): "csv" | "tsv" | "json" | null {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".tsv")) return "tsv";
  if (lower.endsWith(".csv")) return "csv";
  if (lower.endsWith(".json")) return "json";
  return null;
}

/** One line of delimited text into fields, honouring `"quoted, with a delimiter inside"` and `""` as an escaped quote. */
export function splitDelimitedLine(line: string, delimiter: string): string[] {
  const fields: string[] = [];
  let current = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
          current += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        current += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === delimiter) {
      fields.push(current);
      current = "";
    } else {
      current += ch;
    }
  }
  fields.push(current);
  return fields.map((field) => field.trim());
}

/** The first non-empty line of `text` (CSV/TSV's header row), or `""`. */
function firstLine(text: string): string {
  for (const line of text.split(/\r\n|\r|\n/)) {
    if (line.trim() !== "") return line;
  }
  return "";
}

/** Column headers from file `text`, guessed from its `format` — empty when nothing parses. */
export function sniffDatasetColumns(text: string, format: "csv" | "tsv" | "json" | null): string[] {
  if (format === "csv" || format === "tsv") {
    const header = firstLine(text);
    if (!header) return [];
    return splitDelimitedLine(header, format === "tsv" ? "\t" : ",").filter((name) => name !== "");
  }
  if (format === "json") {
    try {
      const parsed: unknown = JSON.parse(text);
      const first = Array.isArray(parsed) ? parsed[0] : undefined;
      if (first && typeof first === "object" && !Array.isArray(first)) {
        return Object.keys(first as Record<string, unknown>);
      }
    } catch {
      return [];
    }
  }
  return [];
}

/** Reads `file` and sniffs its columns (empty array for an unrecognised extension or an unparseable file). */
export async function sniffDatasetFileColumns(file: File): Promise<string[]> {
  const format = datasetFormatFromFilename(file.name);
  if (!format) return [];
  const text = await file.text();
  return sniffDatasetColumns(text, format);
}

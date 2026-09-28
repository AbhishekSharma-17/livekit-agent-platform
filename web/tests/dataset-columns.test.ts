import { describe, expect, it } from "vitest";

import {
  datasetFormatFromFilename,
  sniffDatasetColumns,
  splitDelimitedLine,
} from "@/components/console/datasets/dataset-columns";

describe("datasetFormatFromFilename", () => {
  it("reads the format from the extension, case-insensitively", () => {
    expect(datasetFormatFromFilename("Policies.CSV")).toBe("csv");
    expect(datasetFormatFromFilename("policies.tsv")).toBe("tsv");
    expect(datasetFormatFromFilename("policies.json")).toBe("json");
    expect(datasetFormatFromFilename("policies.xlsx")).toBeNull();
  });
});

describe("splitDelimitedLine", () => {
  it("splits a plain comma line", () => {
    expect(splitDelimitedLine("policy_number,phone,name", ",")).toEqual(["policy_number", "phone", "name"]);
  });

  it("honours a quoted field with the delimiter inside it", () => {
    expect(splitDelimitedLine('"Policy, Number",phone', ",")).toEqual(["Policy, Number", "phone"]);
  });

  it("unescapes a doubled quote inside a quoted field", () => {
    expect(splitDelimitedLine('"Say ""hi""",phone', ",")).toEqual(['Say "hi"', "phone"]);
  });

  it("splits on a tab for tsv", () => {
    expect(splitDelimitedLine("policy_number\tphone", "\t")).toEqual(["policy_number", "phone"]);
  });
});

describe("sniffDatasetColumns", () => {
  it("reads the header row of a csv file", () => {
    expect(sniffDatasetColumns("policy_number,phone,name\n123,555,Jo", "csv")).toEqual([
      "policy_number",
      "phone",
      "name",
    ]);
  });

  it("skips a leading blank line", () => {
    expect(sniffDatasetColumns("\n\npolicy_number,phone\n1,2", "csv")).toEqual(["policy_number", "phone"]);
  });

  it("reads the keys of the first object in a json array", () => {
    expect(sniffDatasetColumns('[{"policy_number": "1", "phone": "555"}]', "json")).toEqual([
      "policy_number",
      "phone",
    ]);
  });

  it("returns nothing for json that isn't an array of objects", () => {
    expect(sniffDatasetColumns('{"a": 1}', "json")).toEqual([]);
    expect(sniffDatasetColumns("not json at all", "json")).toEqual([]);
  });

  it("returns nothing for an unrecognised format", () => {
    expect(sniffDatasetColumns("anything", null)).toEqual([]);
  });
});

import { parseLogLine } from "./LogArtifactViewUtils";

describe("parseLogLine", () => {
  it("should parse log line with prefix and error correctly", () => {
    const logLine = "2023-12-01T10:30:00|ERROR|Something went wrong";

    const result = parseLogLine(logLine);

    expect(result.prefix).toBe("2023-12-01T10:30:00|ERROR|");
    expect(result.content).toEqual("Something went wrong");
    expect(result.isError).toBe(true);
  });
});

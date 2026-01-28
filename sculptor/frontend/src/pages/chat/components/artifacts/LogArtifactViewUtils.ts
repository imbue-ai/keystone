export const parseLogLine = (line: string): { prefix: string; content: string; isError: boolean } => {
  const parts = line.split("|");
  if (parts.length >= 2) {
    const prefix = parts.slice(0, 2).join("|") + "|";
    const content = parts.slice(2).join("|");
    const isError = prefix.includes("ERROR");
    return { prefix, content, isError };
  }
  return { prefix: "", content: line, isError: false };
};

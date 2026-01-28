import type { ChatMessage, TextBlock } from "~/api";
import { isTextBlock } from "~/common/Guards.ts";

type Cursor = {
  blockIndex: number | null;
  offset: number | null;
};

export class StreamingEngine {
  private latestSnapshot: ChatMessage | null = null;
  private cursor: Cursor = {
    blockIndex: null,
    offset: null,
  };

  public updateLatestSnapshot(snapshot: ChatMessage | null): ChatMessage | null {
    this.latestSnapshot = snapshot;

    if (!snapshot) {
      this.cursor = { blockIndex: null, offset: null };
      return null;
    }

    if (!this.isCursorValid()) {
      this.alignCursorToSnapshot(snapshot);
    }

    return this.materialize();
  }

  public flush(): ChatMessage | null {
    if (!this.latestSnapshot) {
      this.cursor = { blockIndex: null, offset: null };
      return null;
    }

    this.alignCursorToSnapshot(this.latestSnapshot);
    return this.materialize();
  }

  public tick(): ChatMessage | null {
    if (!this.latestSnapshot) {
      return null;
    }

    if (!this.isCursorValid()) {
      return this.flush();
    }

    if (this.cursor.blockIndex === null) {
      return this.materialize();
    }

    let activeBlock: ChatMessage["content"][number] | null = this.latestSnapshot.content[this.cursor.blockIndex];
    let isCurrentBlockText = this.cursor.offset !== null && activeBlock !== null && isTextBlock(activeBlock);
    const currentTextLength = isCurrentBlockText ? (activeBlock as TextBlock).text.length : 0;
    const hasRenderedCurrentText = isCurrentBlockText && (this.cursor.offset ?? 0) >= currentTextLength;
    const shouldAdvanceCursor = !isCurrentBlockText || hasRenderedCurrentText;

    if (shouldAdvanceCursor) {
      const nextBlockIndex = this.findNextTextBlock(this.cursor.blockIndex);
      if (nextBlockIndex !== null) {
        this.cursor.blockIndex = nextBlockIndex;
        this.cursor.offset = 0;
      } else {
        this.cursor = this.findTerminalCursorPosition();
        activeBlock = this.getActiveBlock();
        isCurrentBlockText = this.cursor.offset !== null && activeBlock !== null && isTextBlock(activeBlock);
      }
      activeBlock = this.getActiveBlock();
      isCurrentBlockText = this.cursor.offset !== null && activeBlock !== null && isTextBlock(activeBlock);
    }

    if (isCurrentBlockText) {
      const textBlock = activeBlock as TextBlock;
      const existingOffset = this.cursor.offset ?? 0;
      if (existingOffset < textBlock.text.length) {
        const remainingText = textBlock.text.slice(existingOffset);
        const chunks = chunkText(remainingText);
        if (chunks.length > 0) {
          this.cursor.offset = Math.min(textBlock.text.length, existingOffset + chunks[0].length);
        } else {
          this.cursor.offset = textBlock.text.length;
        }
      }
    }

    if (!this.isCursorValid()) {
      return this.flush();
    }

    return this.materialize();
  }

  public hasPendingChunks(): boolean {
    if (!this.latestSnapshot || this.cursor.blockIndex === null) {
      return false;
    }

    const activeBlock = this.latestSnapshot.content[this.cursor.blockIndex];
    if (!activeBlock) {
      return false;
    }

    if (this.cursor.offset === null) {
      return this.findNextTextBlock(this.cursor.blockIndex) !== null;
    }

    if (!isTextBlock(activeBlock)) {
      return false;
    }

    const textLength = activeBlock.text.length;
    if (this.cursor.offset < textLength) {
      return true;
    }

    return this.findNextTextBlock(this.cursor.blockIndex) !== null;
  }

  public render(): ChatMessage | null {
    if (!this.latestSnapshot) {
      return null;
    }

    if (!this.isCursorValid()) {
      return this.flush();
    }
    return this.materialize();
  }

  private getActiveBlock(): ChatMessage["content"][number] | null {
    if (!this.latestSnapshot || this.cursor.blockIndex === null) {
      return null;
    }
    return this.latestSnapshot.content[this.cursor.blockIndex] ?? null;
  }

  private alignCursorToSnapshot(snapshot: ChatMessage): void {
    const tailIndex = this.findTailTextBlockIndex(snapshot);
    if (tailIndex === null) {
      if (snapshot.content.length === 0) {
        this.cursor = { blockIndex: null, offset: null };
        return;
      }
      this.cursor = {
        blockIndex: snapshot.content.length - 1,
        offset: null,
      };
      return;
    }

    const tailBlock = snapshot.content[tailIndex] as TextBlock;
    this.cursor = {
      blockIndex: tailIndex,
      offset: tailBlock.text.length,
    };
  }

  private findTailTextBlockIndex(snapshot: ChatMessage): number | null {
    for (let index = snapshot.content.length - 1; index >= 0; index -= 1) {
      if (isTextBlock(snapshot.content[index])) {
        return index;
      }
    }
    return null;
  }

  private findTerminalCursorPosition(): Cursor {
    if (!this.latestSnapshot || this.latestSnapshot.content.length === 0) {
      return { blockIndex: null, offset: null };
    }

    const lastIndex = this.latestSnapshot.content.length - 1;
    const tailBlockIndex = this.findTailTextBlockIndex(this.latestSnapshot);

    if (tailBlockIndex === null) {
      return {
        blockIndex: lastIndex,
        offset: null,
      };
    }

    const block = this.latestSnapshot.content[tailBlockIndex] as TextBlock;
    return {
      blockIndex: tailBlockIndex,
      offset: block.text.length,
    };
  }

  private materialize(): ChatMessage | null {
    if (!this.latestSnapshot) {
      return null;
    }

    if (this.cursor.blockIndex === null) {
      return this.latestSnapshot;
    }

    const snapshot = this.latestSnapshot;
    const cursorIndex = this.cursor.blockIndex;
    const activeBlock = snapshot.content[cursorIndex];
    const isTextCursor = this.cursor.offset !== null && isTextBlock(activeBlock);
    const textBlock = isTextCursor ? (activeBlock as TextBlock) : null;
    const safeOffset = isTextCursor && textBlock ? Math.min(this.cursor.offset ?? 0, textBlock.text.length) : null;
    const tailTextLength = textBlock?.text.length ?? 0;
    const isTailFullyRendered = !isTextCursor || safeOffset === tailTextLength;
    const content: Array<ChatMessage["content"][number]> = [];

    for (let index = 0; index < snapshot.content.length; index += 1) {
      const block = snapshot.content[index];

      if (index < cursorIndex) {
        content.push(block);
        continue;
      }

      if (index === cursorIndex) {
        if (!isTextCursor || safeOffset === null || !textBlock) {
          content.push(block);
        } else {
          content.push({
            ...block,
            text: textBlock.text.slice(0, safeOffset),
          });
          if (safeOffset < textBlock.text.length) {
            break;
          }
        }

        if (!isTailFullyRendered) {
          break;
        }
        continue;
      }

      if (!isTailFullyRendered) {
        break;
      }

      content.push(block);
    }

    return {
      ...snapshot,
      content,
    };
  }

  private findNextTextBlock(startingIndex: number): number | null {
    if (!this.latestSnapshot) {
      return null;
    }

    for (let i = startingIndex + 1; i < this.latestSnapshot.content.length; i += 1) {
      if (isTextBlock(this.latestSnapshot.content[i])) {
        return i;
      }
    }
    return null;
  }

  private isCursorValid(): boolean {
    if (this.cursor.blockIndex === null) {
      if (this.cursor.offset !== null) {
        return false;
      }

      if (!this.latestSnapshot) {
        return true;
      }

      return this.findTailTextBlockIndex(this.latestSnapshot) === null;
    }

    if (!this.latestSnapshot) {
      return false;
    }

    if (this.cursor.blockIndex < 0 || this.cursor.blockIndex >= this.latestSnapshot.content.length) {
      return false;
    }

    const block = this.latestSnapshot.content[this.cursor.blockIndex];

    if (this.cursor.offset === null) {
      return !isTextBlock(block);
    }

    if (!isTextBlock(block)) {
      return false;
    }

    if (this.cursor.offset < 0) {
      return false;
    }

    const text = (block as TextBlock).text;
    return this.cursor.offset <= text.length;
  }
}

const chunkText = (text: string): Array<string> => {
  const tokens: Array<string> = [];
  const regex = /[^\s]+(?:\s+)?/g;
  let match: RegExpExecArray | null;

  while ((match = regex.exec(text)) !== null) {
    tokens.push(match[0] ?? "");
  }

  return tokens;
};

export const registerEngine = (engine: StreamingEngine): void => {
  if (activeEngine && activeEngine !== engine) {
    throw new Error("StreamingEngine already registered. Only one stream may be active at a time.");
  }
  activeEngine = engine;
};

export const unregisterEngine = (engine: StreamingEngine): void => {
  if (activeEngine === engine) {
    activeEngine = null;
  }
};

export let activeEngine: StreamingEngine | null = null;

import "highlight.js/styles/github.css";

import { Box, ScrollArea } from "@radix-ui/themes";
import hljs from "highlight.js/lib/core";
import css from "highlight.js/lib/languages/css";
import javascript from "highlight.js/lib/languages/javascript";
import python from "highlight.js/lib/languages/python";
import typescript from "highlight.js/lib/languages/typescript";
import html from "highlight.js/lib/languages/xml";
import type { ReactElement } from "react";
import { memo, useMemo } from "react";
import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkEmoji from "remark-emoji";
import remarkGfm from "remark-gfm";

import { Code } from "./Code";
import styles from "./MarkdownBlock.module.scss";

// Register languages
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("js", javascript);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("ts", typescript);
hljs.registerLanguage("tsx", typescript);
hljs.registerLanguage("python", python);
hljs.registerLanguage("css", css);
hljs.registerLanguage("html", html);
hljs.registerLanguage("xml", html);

const MemoizedInlineCode = memo(({ children }: { children: React.ReactNode }) => {
  return <Code className={styles.inlineCode}>{children}</Code>;
});

const MemoizedCodeBlock = memo(({ content, language }: { content: string; language: string }) => {
  const highlightedCode = useMemo(() => {
    try {
      if (language && hljs.getLanguage(language)) {
        return hljs.highlight(content, { language }).value;
      } else {
        return hljs.highlightAuto(content).value;
      }
    } catch (error) {
      console.error("Error highlighting code:", error);
      return content;
    }
  }, [content, language]);

  return (
    <ScrollArea className={styles.codeBlock} scrollbars="horizontal" type="hover" size="1">
      <div dangerouslySetInnerHTML={{ __html: highlightedCode }} />
    </ScrollArea>
  );
});

export const MarkdownBlock = memo((props: { content: string }): ReactElement => {
  const components = useMemo<Components>(
    () => ({
      code: (props): React.ReactElement => {
        const { children, className } = props;
        if (!children) {
          return <></>;
        }
        const match = /language-(\w+)/.exec(className || "");
        const language = match ? match[1] : "";
        const isInline = children.toString().slice(-1) !== "\n";

        if (isInline) {
          return <MemoizedInlineCode>{children}</MemoizedInlineCode>;
        }

        const codeContent = children.toString();
        const codeBlockKey = `${language}:${codeContent.substring(0, 40)}`;

        return <MemoizedCodeBlock key={codeBlockKey} content={codeContent} language={language} />;
      },
      table: (props): React.ReactElement => {
        return (
          <ScrollArea scrollbars="horizontal" type="hover" size="1">
            <table {...props} />
          </ScrollArea>
        );
      },
      h1: "h2",
      h2: "h3",
      h3: "strong",
      a: ({ children, href }): React.ReactElement => {
        return (
          <a href={href} target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        );
      },
    }),
    [],
  );

  return (
    <Box className={styles.markdownContainer}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkEmoji]} components={components}>
        {props.content}
      </ReactMarkdown>
    </Box>
  );
});

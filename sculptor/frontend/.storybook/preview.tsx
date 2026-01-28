import { Flex } from "@radix-ui/themes";
import "@radix-ui/themes/styles.css";
import type { Preview } from "@storybook/react";
import { Provider as JotaiProvider } from "jotai/react";
import { ThemeProvider } from "../src/components/ThemeProvider";
import { globalJotaiStore } from "../src/GlobalState";
import "../src/index.css";

const preview: Preview = {
  parameters: {
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /Date$/i,
      },
    },
  },
};

export default preview;

export const decorators = [
  (Story) => {
    return (
      <div data-testid="storybook-root">
        <JotaiProvider store={globalJotaiStore}>
          <ThemeProvider>
            <Flex width="100%" height="100%" align="center" justify="center">
              <Story />
            </Flex>
          </ThemeProvider>
        </JotaiProvider>
      </div>
    );
  },
];

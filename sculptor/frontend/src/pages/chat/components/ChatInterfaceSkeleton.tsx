import { Box, Flex, Link, Skeleton, Spinner, Text } from "@radix-ui/themes";
import type { ReactElement } from "react";

import { ElementIds, LlmModel } from "../../../api/types.gen.ts";
import { ChatInput } from "./ChatInput.tsx";
import styles from "./ChatInterface.module.scss";

export const ChatMessagesSkeleton = (): ReactElement => {
  return (
    <>
      {FAKE_CHAT_MESSAGES.map(
        (message): ReactElement => (
          <Box key={message.id} width="100%" mt="6">
            <Box px="4" py="2" className={styles.userMessage} data-testid={ElementIds.USER_MESSAGE}>
              {message.content.map(
                (block, index) =>
                  block.type === "text" && (
                    <Skeleton key={index}>
                      <Text key={index}>{block.text}</Text>,
                    </Skeleton>
                  ),
              )}
            </Box>
          </Box>
        ),
      )}
    </>
  );
};

export const ChatInterfaceSkeleton = (): ReactElement => {
  return (
    <Flex
      direction="column"
      className={styles.mainContent}
      pb="4"
      align="center"
      position="relative"
      data-testid={ElementIds.CHAT_PANEL}
    >
      <div className={styles.spacer} />
      <Flex direction="column" maxWidth="100%" className={styles.userMessage} overflow="hidden">
        <ChatMessagesSkeleton />
      </Flex>

      <ChatInput systemPrompt="" model={LlmModel.CLAUDE_4_SONNET} isDisabled={true} />
    </Flex>
  );
};

type ChatBuildingSkeletonProps = {
  onViewLogsClick?: () => void;
};

export const ChatBuildingSkeleton = ({ onViewLogsClick }: ChatBuildingSkeletonProps): ReactElement => {
  return (
    <Flex direction="column" className={styles.mainContent} pb="4" align="center" position="relative">
      <div className={styles.spacer} />
      <Flex direction="row" minWidth="100%" justify="between">
        <Flex direction="row" align="center" gap="8px" className={styles.buildingMessage} overflow="hidden">
          <Spinner />
          Building environment ...
        </Flex>
        <Link onClick={onViewLogsClick}>View log</Link>
      </Flex>
    </Flex>
  );
};

const FAKE_CHAT_MESSAGES = [
  {
    id: "1",
    role: "user",
    content: [
      {
        type: "text",
        text: "Hey there! I’ve been hacking away on this little side project over the weekend, and I think I hit a wall. It’s a React app that pulls in live weather data, and while most of it is working fine, there’s a really annoying issue I can’t seem to debug. Figured I’d ping you before I fall down another 3-hour StackOverflow rabbit hole 😅. Mind helping me take a look?",
      },
    ],
    timestamp: new Date().toISOString(),
  },
  {
    id: "2",
    role: "assistant",
    content: [
      {
        type: "text",
        text: "Of course! I’d be glad to help you figure this out. Weather apps can actually be sneakily tricky, especially when APIs, caching, and reactivity are all in play. Why don’t you tell me a bit more about how your app is structured? What libraries or tools are you using, and what’s the specific behavior that’s giving you trouble? If you’ve got any error messages or weird logs too, feel free to share those—I can help you make sense of them.",
      },
    ],
    timestamp: new Date().toISOString(),
  },
  {
    id: "3",
    role: "user",
    content: [
      {
        type: "text",
        text: "Yeah totally. So the app uses the browser’s geolocation API to get the user’s current coordinates, then calls OpenWeather’s API to fetch the local weather forecast. I’m using React Query to manage the data fetching and caching — it was going really smoothly until I noticed this weird edge case: if I refresh the page, it *looks* like the API is called again, but the UI doesn’t always update. The data sometimes stays stale, like it’s pulling from an old cache. No errors or anything, just silently stale data that makes me question reality.",
      },
    ],
    timestamp: new Date().toISOString(),
  },
  {
    id: "4",
    role: "assistant",
    content: [
      {
        type: "text",
        text: "Ahhh, yep, that sounds very familiar. React Query is powerful, but its caching strategy can catch you off guard if you’re not fully aware of the defaults. By default, it assumes your data is fresh for a few minutes, and it won’t re-fetch unless certain conditions are met. That means if your user refreshes the page, React Query might still serve what it thinks is 'fresh' data from its cache instead of triggering a new fetch. You can override this behavior by setting `staleTime` to `0`, which basically tells React Query: 'hey, treat this data as stale immediately, every single time.' You can also manually trigger a refetch if you want tighter control. Want me to walk you through what the code would look like?",
      },
    ],
    timestamp: new Date().toISOString(),
  },
  {
    id: "5",
    role: "user",
    content: [
      {
        type: "text",
        text: "Yes please — that would be amazing. I read a bit about staleTime but wasn’t sure if that was the right thing to mess with. I’d love to see a snippet showing how to use `staleTime: 0` in a `useQuery`, and also how to let the user trigger a refetch manually just in case. Right now, the UI doesn’t have a refresh button, but I’ve been thinking of adding one anyway just to make things feel more responsive.",
      },
    ],
    timestamp: new Date().toISOString(),
  },
  {
    id: "6",
    role: "assistant",
    content: [
      {
        type: "text",
        text: "Totally makes sense — having a manual refresh button is a great UX affordance, especially for real-time-ish data like weather. Here’s an example of how you might set this up using React Query’s `useQuery`:\n\n```ts\nconst { data, refetch, isFetching } = useQuery(['weather', location], fetchWeather, {\n  staleTime: 0,\n  cacheTime: 1000 * 60 * 5, // optional: keep cache for 5 mins in background\n});\n\nreturn (\n  <div>\n    <h1>Current Temperature: {data?.temp}°C</h1>\n    <button onClick={() => refetch()} disabled={isFetching}>\n      {isFetching ? 'Refreshing…' : 'Refresh Weather'}\n    </button>\n  </div>\n);\n```\n\nThe `staleTime: 0` ensures React Query always considers the data stale on mount, triggering a fetch. The `refetch()` gives users a way to force an update. Bonus: `isFetching` lets you show loading indicators without blocking the UI. Let me know if you need help wiring up the fetchWeather function too.",
      },
    ],
    timestamp: new Date().toISOString(),
  },
  {
    id: "7",
    role: "user",
    content: [
      {
        type: "text",
        text: "This is exactly what I needed — thank you!! The `isFetching` bit is a nice touch too; I always forget about that. I’ll integrate this and see if it solves the weird behavior I was seeing. Honestly, it’s wild how much invisible logic lives in these frameworks. I assumed data fetching was just fetch → render, but the caching layer is sneakily powerful. I’ll report back if I run into anything else weird, but this gives me a super solid starting point 🙌",
      },
    ],
    timestamp: new Date().toISOString(),
  },
  {
    id: "8",
    role: "assistant",
    content: [
      {
        type: "text",
        text: "You nailed it — frameworks do a lot behind the scenes, and while that’s great for performance and developer experience, it can also mean you hit moments where things feel like black magic. But once you learn how to tame the caching dragon, it becomes a powerful ally! Definitely let me know how it goes, and if you want to go deeper into React Query’s other features like background refetching or dependent queries, just say the word. Always happy to nerd out with you 🤓",
      },
    ],
    timestamp: new Date().toISOString(),
  },
];

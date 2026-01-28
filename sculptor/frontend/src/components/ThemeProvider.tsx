import { Theme as RadixTheme } from "@radix-ui/themes";
import type { PropsWithChildren, ReactElement } from "react";

import { useResolvedTheme } from "../common/Utils.ts";

export const ImbueTheme = ({ children }: PropsWithChildren): ReactElement => {
  const theme = useResolvedTheme();
  return (
    <RadixTheme accentColor="gold" grayColor="sand" appearance={theme}>
      {children}
    </RadixTheme>
  );
};

export const ThemeProvider = ({ children }: PropsWithChildren): ReactElement => {
  return <ImbueTheme>{children}</ImbueTheme>;
};

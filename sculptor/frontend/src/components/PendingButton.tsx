import { Button, type ButtonProps, Spinner } from "@radix-ui/themes";
import type { PropsWithChildren } from "react";
import type React from "react";
import { forwardRef, useState } from "react";

import type { PropsWithClassName } from "../common/Types.ts";
import { mergeClasses } from "../common/Utils.ts";

type PendingButtonProps = ButtonProps & PropsWithClassName & PropsWithChildren;

export const PendingButton = forwardRef<HTMLButtonElement, PendingButtonProps>((props, ref): React.ReactElement => {
  const [isPending, setIsPending] = useState(false);
  // eslint-disable-next-line @typescript-eslint/naming-convention
  const { children, className, onClick, loading, ...buttonProps } = props;

  const handleClick = async (event: React.MouseEvent<HTMLButtonElement>): Promise<void> => {
    setIsPending(true);
    if (onClick) {
      console.log("doing async event", { event });
      await onClick(event);
      console.log("done async event", { event });
    }
    // FIXME: this is a hack to make the spinner look smooth, we should use a better way to do this
    //  this is really, really, really bad please fix this
    setTimeout(() => {
      setIsPending(false);
    }, 50);
  };

  const isLoading = isPending || !!loading;
  return (
    <Button
      {...buttonProps}
      ref={ref}
      onClick={handleClick}
      className={mergeClasses(className)}
      disabled={!!buttonProps.disabled || isLoading}
    >
      {isLoading && <Spinner size="1" />}
      {children}
    </Button>
  );
});

import { useLocation, useNavigate, useParams } from "react-router-dom";

export class RouteError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RouteError";
    Object.setPrototypeOf(this, RouteError.prototype);
  }
}

export class MissingURLArgument extends RouteError {
  constructor(arg: string) {
    super(`Missing URL argument: ${arg}`);
    this.name = "MissingURLArgument";
    Object.setPrototypeOf(this, MissingURLArgument.prototype);
  }
}

type ImbueNavigationFunctions = {
  navigateToHome: (projectID: string) => void;
  navigateToChat: (projectID: string, taskID: string) => void;
  navigateHomeNoProject: () => void;
  navigateToSettings: (projectID: string) => void;
};

export const useImbueNavigate = (): ImbueNavigationFunctions => {
  const defaultNavigateFn = useNavigate();

  const navigate = (to: string): void => {
    console.log(`navigating to: ${to}`);
    defaultNavigateFn(to);
  };

  // TODO: it might be possible to automatically create this in the future based on our routes
  return {
    navigateToHome: (projectID: string): void => {
      navigate(`/projects/${projectID}`);
    },
    navigateToChat: (projectID: string, taskID: string): void => {
      navigate(`/projects/${projectID}/chat/${taskID}`);
    },
    navigateHomeNoProject: (): void => {
      navigate(`/`);
    },
    navigateToSettings: (projectID: string): void => {
      navigate(`/projects/${projectID}/settings`);
    },
  };
};

type ImbueLocationType = {
  isHomeRoute: boolean;
  isChatRoute?: boolean;
};

export const useImbueLocation = (): ImbueLocationType => {
  const location = useLocation();
  const pathname = location.pathname;

  const isChatRoute = /^\/projects\/[^/]+\/chat\/[^/]+$/.test(pathname);
  const isHomeRoute = /^\/projects\/[^/]+$/.test(pathname);

  return {
    isHomeRoute,
    isChatRoute,
  };
};

class ExpectedParamsNotFoundError extends Error {}

export type PossibleURLParams = {
  projectID?: string;
  taskID?: string;
};

export const useImbueParams = (): PossibleURLParams => {
  return useParams<PossibleURLParams>();
};

export type ProjectPageParams = PossibleURLParams & {
  projectID: string;
};

const isProjectPageParams = (params: PossibleURLParams): params is ProjectPageParams => {
  return params.projectID !== undefined && params.projectID !== null;
};

export const useProjectPageParams = (): ProjectPageParams => {
  const location = useLocation();
  const params = useParams<PossibleURLParams>();
  if (!isProjectPageParams(params)) {
    throw new ExpectedParamsNotFoundError(
      `Expected URL ${location.pathname} to contain projectID but only extracted the following: ${JSON.stringify(params)}`,
    );
  }
  return params;
};

export type TaskPageParams = ProjectPageParams & {
  taskID: string;
};

const isTaskPageParams = (params: PossibleURLParams): params is TaskPageParams => {
  return isProjectPageParams(params) && params.taskID !== undefined && params.taskID !== null;
};

export const useTaskPageParams = (): TaskPageParams => {
  const location = useLocation();
  const params = useParams<PossibleURLParams>();
  if (!isTaskPageParams(params)) {
    throw new ExpectedParamsNotFoundError(
      `Expected URL ${location.pathname} to contain projectID and taskID but only extracted the following: ${JSON.stringify(
        params,
      )}`,
    );
  }
  return params;
};

export const HOME_ROUTE = "HOME_ROUTE" as const;
export const CHAT_ROUTE = "CHAT_ROUTE" as const;
export const NOT_FOUND_ROUTE = "NOT_FOUND_ROUTE" as const;

const HOME_URL_PART: string = "";
const CHAT_URL_PART: string = "chat";

export type RouteNames = typeof HOME_ROUTE | typeof CHAT_ROUTE | typeof NOT_FOUND_ROUTE;

export type Route = {
  name: RouteNames;
  parts: ReadonlyArray<string>;
};

export const ROUTES = [
  {
    name: HOME_ROUTE,
    parts: [`${HOME_URL_PART}/projects/:projectID`],
  },
  {
    name: CHAT_ROUTE,
    parts: [`/projects/:projectID/${CHAT_URL_PART}/:taskID`],
  },
  {
    name: NOT_FOUND_ROUTE,
    parts: ["*"],
  },
] as const satisfies ReadonlyArray<Route>;

export const PATH_BY_ROUTE_NAME = ROUTES.reduce(
  (acc, route) => {
    acc[route.name] = route.parts.join("/") || "/";
    return acc;
  },
  {} as Record<string, string>,
);

// TODO: this function could use some tests
export const getMatchingRouteName = (path: string): RouteNames => {
  const matchingRoute = ROUTES.filter((route) => route.name !== NOT_FOUND_ROUTE).find((route) => {
    const regexPath = PATH_BY_ROUTE_NAME[route.name];
    const regexString = regexPath.replace(/:[^/]+/g, "([^/]+)");
    const regex = new RegExp(`^${regexString}/?$`);
    return regex.test(path);
  });

  if (matchingRoute) {
    return matchingRoute.name;
  }

  return NOT_FOUND_ROUTE;
};

export const buildUrlForRoute = (routeName: RouteNames, params: Record<string, string>): string => {
  const path = PATH_BY_ROUTE_NAME[routeName];
  let newPath = path;
  for (const [key, value] of Object.entries(params)) {
    // if not in path we need to throw an error
    if (!path.includes(`:${key}`)) {
      throw new Error(`Key ${key} not found in path ${path}`);
    }
    newPath = newPath.replace(`:${key}`, encodeURIComponent(value));
  }
  return newPath;
};

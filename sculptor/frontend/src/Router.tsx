import type { ReactElement } from "react";
import type { LoaderFunctionArgs } from "react-router-dom";
import { redirect } from "react-router-dom";
import { createHashRouter, RouterProvider } from "react-router-dom";

import { setMostRecentlyUsedProject } from "./api";
import { setupAuthHeaders } from "./common/Auth.ts";
import { ProjectLayout } from "./layouts/ProjectLayout";
import { ChatPage } from "./pages/chat/ChatPage";
import { NotFoundErrorPage } from "./pages/error/NotFound.tsx";
import { RouteErrorPage } from "./pages/error/RouteErrorPage.tsx";
import { HomePage } from "./pages/home/HomePage.tsx";
import { SelectProjectPage } from "./pages/select-project/SelectProjectPage.tsx";
import { SettingsPage } from "./pages/settings/SettingsPage.tsx";

const projectLoader = async ({ params }: LoaderFunctionArgs): Promise<Response | null> => {
  try {
    const headers = new Headers();
    setupAuthHeaders(headers);
    await setMostRecentlyUsedProject({ path: { project_id: params.projectID! }, meta: { skipWsAck: true }, headers });
    return null;
  } catch (error) {
    console.error("Failed to set most recently used project:", error);
    return null;
  }
};

const router = createHashRouter([
  {
    path: "/",
    loader: (): Response => redirect("/projects"),
    errorElement: <RouteErrorPage />,
  },
  {
    path: "/projects",
    element: <SelectProjectPage />,
    errorElement: <RouteErrorPage />,
  },
  {
    path: "/projects/:projectID",
    loader: projectLoader,
    element: <ProjectLayout />,
    errorElement: <RouteErrorPage />,
    children: [
      {
        index: true,
        element: <HomePage />,
      },
      {
        path: "chat/:taskID",
        element: <ChatPage />,
      },
      {
        path: "settings",
        element: <SettingsPage />,
      },
    ],
  },
  {
    path: "*",
    element: <NotFoundErrorPage />,
  },
]);

export const Router = (): ReactElement => {
  return <RouterProvider router={router} />;
};

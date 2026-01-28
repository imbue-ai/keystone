import { useAtomValue } from "jotai";
import type { ReactElement } from "react";
import { useEffect, useRef, useState } from "react";

import type { Notification } from "../api";
import { NotificationImportance } from "../api";
import { useImbueParams } from "../common/NavigateUtils";
import { notificationsAtom } from "../common/state/atoms/notifications";
import { Toast, ToastType } from "./Toast";

type NotificationToastState = {
  notification: Notification;
  isOpen: boolean;
};

const getToastType = (importance?: NotificationImportance): ToastType => {
  switch (importance) {
    case NotificationImportance.CRITICAL:
      return ToastType.ERROR;
    case NotificationImportance.TIME_SENSITIVE:
      return ToastType.WARNING;
    case NotificationImportance.ACTIVE:
      return ToastType.DEFAULT;
    case NotificationImportance.PASSIVE:
    case undefined:
    default:
      return ToastType.DEFAULT;
  }
};

const getToastDurationMiliseconds = (importance?: NotificationImportance): number => {
  switch (importance) {
    case NotificationImportance.CRITICAL:
      return 10000;
    case NotificationImportance.TIME_SENSITIVE:
      return 5000;
    case NotificationImportance.ACTIVE:
    case NotificationImportance.PASSIVE:
    case undefined:
    default:
      return 3000;
  }
};

/**
 * Component that displays notifications from the notificationsAtom as bottom-right toasts.
 * Automatically manages showing new notifications and dismissing them after a duration.
 */
export const NotificationToasts = (): ReactElement => {
  const notifications = useAtomValue(notificationsAtom);
  const { projectID, taskID } = useImbueParams();
  const [toastStates, setToastStates] = useState<Array<NotificationToastState>>([]);
  const notificationIdsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const processedNotificationIds = notificationIdsRef.current;
    const newNotifications = notifications.filter(
      (notification) => !processedNotificationIds.has(notification.objectId),
    );

    if (newNotifications.length > 0) {
      const newToastStates = newNotifications
        .filter((notification) => {
          // Discard notifications not relevant to the current project/task.
          return (
            (!notification.projectId || notification.projectId === projectID) &&
            (!notification.taskId || notification.taskId === taskID)
          );
        })
        .map((notification) => ({
          notification,
          isOpen: true,
        }));

      setToastStates((prev) => [...prev, ...newToastStates]);

      newNotifications.forEach((n) => processedNotificationIds.add(n.objectId));
    }
  }, [projectID, taskID, notifications]);

  const handleOpenChange = (index: number, open: boolean): void => {
    if (!open) {
      setToastStates((prev) => prev.filter((_, i) => i !== index));
    }
  };

  return (
    <>
      {toastStates.map((toastState, index) => (
        <Toast
          key={toastState.notification.objectId}
          open={toastState.isOpen}
          onOpenChange={(open) => handleOpenChange(index, open)}
          title={toastState.notification.message}
          type={getToastType(toastState.notification.importance)}
          duration={getToastDurationMiliseconds(toastState.notification.importance)}
        />
      ))}
    </>
  );
};

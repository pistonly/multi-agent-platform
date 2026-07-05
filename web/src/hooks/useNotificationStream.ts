import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { streamNotifications } from "../api/client";

export function useNotificationStream(token: string | null, enabled: boolean) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!enabled || !token) return;

    const controller = new AbortController();

    void streamNotifications(token, {
      signal: controller.signal,
      onEvent: (event) => {
        if (event.type === "notification.created") {
          void queryClient.invalidateQueries({ queryKey: ["notifications"] });
          void queryClient.invalidateQueries({ queryKey: ["work"] });
        }
      },
      onError: () => {
        // fetch 断线后由浏览器重连逻辑在 streamNotifications 内处理
      },
    });

    return () => controller.abort();
  }, [enabled, token, queryClient]);
}

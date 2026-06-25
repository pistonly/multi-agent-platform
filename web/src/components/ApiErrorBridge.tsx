import { useEffect } from "react";
import { setApiErrorHandler } from "../api/client";
import { useToast } from "../context/ToastContext";

export function ApiErrorBridge() {
  const { showToast } = useToast();

  useEffect(() => {
    setApiErrorHandler((message) => showToast(message, "error"));
    return () => setApiErrorHandler(null);
  }, [showToast]);

  return null;
}

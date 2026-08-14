import { useQuery } from "@tanstack/react-query";
import { fetchDoc } from "../api/client";

export function useDoc(projectId: string | null, path: string | null | undefined) {
  return useQuery({
    queryKey: ["doc", path],
    queryFn: () => fetchDoc(projectId!, path!),
    enabled: !!projectId && !!path,
    staleTime: 30_000,
  });
}

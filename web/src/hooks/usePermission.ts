import { useAuthStore } from "@/stores/authStore";

export function usePermission() {
  const user = useAuthStore((s) => s.user);

  return {
    can: (_resource: string, _action: string) => {
      if (!user) return false;
      return true;
    },
  };
}

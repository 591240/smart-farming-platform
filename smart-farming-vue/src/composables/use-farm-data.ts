// 用户农场/圈舍数据 — 看板与场舍页的统一数据源（Vue Query 缓存共享）

import { computed } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { apiListFarms, apiListPens, type UserFarm, type UserPen } from '@/api/auth-api';

export type { UserFarm, UserPen };

const FARMS_KEY = 'user-farms';
const pensKey = (farmId: number) => ['user-pens', farmId] as const;

export function useUserFarms() {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: [FARMS_KEY],
    queryFn: async () => (await apiListFarms()).items,
    staleTime: 30_000,
  });

  return {
    farms: computed<UserFarm[]>(() => query.data.value ?? []),
    loading: query.isLoading,
    // 取失败与"确实没有场"必须能区分：否则后端一挂就显示「还没有养殖场，去创建」，
    // 农户会以为自己数据丢了，或者更糟——照着提示去新建一个重复的场。
    error: query.isError,
    reload: () => qc.invalidateQueries({ queryKey: [FARMS_KEY] }),
  };
}

export function useUserPens(farmId: import('vue').Ref<number | null | undefined> | number | null | undefined) {
  const qc = useQueryClient();
  const id = computed(() => (typeof farmId === 'object' && farmId !== null ? farmId.value : farmId));
  const query = useQuery({
    queryKey: computed(() => pensKey(id.value ?? 0)),
    queryFn: async () => (await apiListPens(id.value as number)).items,
    enabled: computed(() => !!id.value),
    staleTime: 30_000,
  });

  return {
    pens: computed<UserPen[]>(() => query.data.value ?? []),
    loading: query.isLoading,
    reload: () => {
      qc.invalidateQueries({ queryKey: pensKey(id.value ?? 0) });
      qc.invalidateQueries({ queryKey: [FARMS_KEY] });
    },
  };
}

export function usePrimaryFarm() {
  const { farms } = useUserFarms();
  return computed<UserFarm | null>(() => farms.value[0] ?? null);
}

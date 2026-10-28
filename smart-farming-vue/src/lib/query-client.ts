import { QueryClient } from '@tanstack/vue-query';

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 后端重启或网络抖动的典型形态是连续几个 5xx。默认 retry=3 配指数退避，再叠上
      // 大屏的 15s/60s 轮询，一个坏接口就够把入口打成请求风暴——所以只留一次重试。
      retry: 1,
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
      // 大屏多张卡共用同一份数据，5s 内视为新鲜，避免同帧重复请求
      staleTime: 5_000,
      // 大屏是常驻展示页：切个窗口回来就全量重取既无意义又放大抖动
      refetchOnWindowFocus: false,
    },
  },
});

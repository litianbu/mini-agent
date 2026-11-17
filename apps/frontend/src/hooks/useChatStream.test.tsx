/**
 * 前端的取消：卸载时要中止进行中的流。
 *
 * 为什么这条值得单独测：
 *   后端做了取消传播（连接断开就停手），但"连接断不断"是前端决定的。
 *   如果组件卸载了却不停请求，后端会以为用户还在看，
 *   继续跑完整个流程（最多 8 轮工具调用）—— 为已经离开的人烧钱。
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useChatStream } from "./useChatStream";

/** 造一个永不结束的流，这样只有"中止"才能让它停。 */
function hangingStream(signal: AbortSignal): Promise<Response> {
  return new Promise((_, reject) => {
    signal.addEventListener("abort", () => {
      reject(new DOMException("aborted", "AbortError"));
    });
  });
}

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useChatStream 的取消", () => {
  it("组件卸载时会中止进行中的请求", async () => {
    let captured: AbortSignal | undefined;

    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init: RequestInit) => {
        captured = init.signal ?? undefined;
        return hangingStream(init.signal!);
      }),
    );

    const { result, unmount } = renderHook(() => useChatStream(), { wrapper });

    act(() => {
      result.current.send("你好");
    });

    await waitFor(() => expect(captured).toBeDefined());
    expect(captured!.aborted).toBe(false);

    // 模拟用户跳到别的页面
    unmount();

    await waitFor(() => expect(captured!.aborted).toBe(true));
  });

  it("stop() 能中止，并且不算错误", async () => {
    let captured: AbortSignal | undefined;

    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init: RequestInit) => {
        captured = init.signal ?? undefined;
        return hangingStream(init.signal!);
      }),
    );

    const { result } = renderHook(() => useChatStream(), { wrapper });

    act(() => {
      result.current.send("你好");
    });
    await waitFor(() => expect(captured).toBeDefined());

    act(() => {
      result.current.stop();
    });

    // 用户主动停止：不该显示成错误
    await waitFor(() => expect(result.current.status).toBe("done"));
    expect(result.current.error).toBe("");
  });
});

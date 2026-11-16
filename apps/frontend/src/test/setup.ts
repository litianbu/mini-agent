/**
 * 测试环境的垫片。
 *
 * jsdom 不是真浏览器，缺几个 antd 会用到的 API。
 * 不补的话，一渲染 antd 组件就报 "matchMedia is not a function"，
 * 测试还没开始就挂了。
 */
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// antd 的响应式栅格会调这个
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// antd 的滚动组件依赖 ResizeObserver
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;

// 每个用例结束后卸载组件，避免上一个的 DOM 残留影响下一个
afterEach(() => cleanup());

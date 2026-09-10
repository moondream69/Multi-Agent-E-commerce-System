import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// 每个用例后卸载组件(tree 清理,避免用例间 DOM 串扰)
afterEach(() => cleanup());

import type { ComponentProps } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { historyApi } from '../../../api/history';
import { ReportMarkdownPanel } from '../ReportMarkdownPanel';

vi.mock('../../../api/history', () => ({
  historyApi: {
    getMarkdown: vi.fn(),
    getShareImage: vi.fn(),
  },
}));

const renderPanel = (props: Partial<ComponentProps<typeof ReportMarkdownPanel>> = {}) => render(
  <UiLanguageProvider>
    <ReportMarkdownPanel
      recordId={7}
      stockName="贵州茅台"
      stockCode="600519"
      createdAt="2026-10-30 15:01:02"
      onRequestClose={() => undefined}
      {...props}
    />
  </UiLanguageProvider>,
);

describe('ReportMarkdownPanel export PDF', () => {
  beforeEach(() => {
    vi.mocked(historyApi.getMarkdown).mockResolvedValue('# 报告\n\n正文内容');
    document.title = 'DSA 股票分析';
    window.localStorage.setItem('dsa.uiLanguage', 'zh');
  });

  afterEach(() => {
    window.dispatchEvent(new Event('afterprint'));
    vi.restoreAllMocks();
  });

  it('calls window.print with the report file name as title and restores it after printing', async () => {
    let titleDuringPrint = '';
    const print = vi.spyOn(window, 'print').mockImplementation(() => {
      titleDuringPrint = document.title;
    });

    renderPanel();
    const button = await screen.findByRole('button', { name: '导出 PDF' });
    await screen.findByText('正文内容');
    expect(button).toBeEnabled();
    expect(button).toHaveTextContent('导出 PDF');

    fireEvent.click(button);

    expect(print).toHaveBeenCalledTimes(1);
    expect(titleDuringPrint).toBe('贵州茅台_600519_2026-10-30_分析报告');

    act(() => {
      window.dispatchEvent(new Event('afterprint'));
    });
    expect(document.title).toBe('DSA 股票分析');
  });

  it('replaces illegal characters in the stock name and uses the market review pattern', async () => {
    let titleDuringPrint = '';
    vi.spyOn(window, 'print').mockImplementation(() => {
      titleDuringPrint = document.title;
    });

    const { unmount } = renderPanel({ stockName: 'A/B:"C"*?<>|', stockCode: 'X\\Y' });
    await screen.findByText('正文内容');
    fireEvent.click(screen.getByRole('button', { name: '导出 PDF' }));
    expect(titleDuringPrint).toBe('A_B_C_X_Y_2026-10-30_分析报告');
    expect(titleDuringPrint).not.toMatch(/[/\\:*?"<>|]/);
    unmount();

    renderPanel({ reportType: 'market_review', stockName: '大盘复盘', stockCode: 'MARKET', marketRegion: 'cn' });
    await screen.findByText('正文内容');
    fireEvent.click(screen.getByRole('button', { name: '导出 PDF' }));
    expect(titleDuringPrint).toBe('A股_大盘复盘_2026-10-30');
  });

  it('is disabled until the report content is loaded', async () => {
    let resolve: (value: string) => void = () => undefined;
    vi.mocked(historyApi.getMarkdown).mockReturnValue(new Promise<string>((r) => {
      resolve = r;
    }));
    renderPanel();
    expect(screen.getByRole('button', { name: '导出 PDF' })).toBeDisabled();
    await act(async () => {
      resolve('# done');
    });
    expect(screen.getByRole('button', { name: '导出 PDF' })).toBeEnabled();
  });

  it('renders the English label under the English UI language', async () => {
    window.localStorage.setItem('dsa.uiLanguage', 'en');
    renderPanel();
    await screen.findByText('正文内容');
    expect(screen.getByRole('button', { name: 'Export PDF' })).toHaveTextContent('Export PDF');
    expect(screen.queryByRole('button', { name: '导出 PDF' })).not.toBeInTheDocument();
  });

  it('prints the stock header meta and the risk disclaimer', async () => {
    renderPanel();
    await screen.findByText('正文内容');
    expect(screen.getByTestId('report-print-meta')).toHaveTextContent('贵州茅台 · 600519 · 分析时间');
    expect(screen.getByTestId('report-print-disclaimer')).toHaveTextContent('仅供研究交流，不构成投资建议');
  });
});

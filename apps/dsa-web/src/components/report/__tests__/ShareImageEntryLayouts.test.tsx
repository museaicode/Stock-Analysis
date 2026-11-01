import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { historyApi } from '../../../api/history';
import { ReportMarkdownPanel } from '../ReportMarkdownPanel';
import { ReportOverview } from '../ReportOverview';

vi.mock('../../../api/history', () => ({
  historyApi: {
    getMarkdown: vi.fn(),
    getShareImage: vi.fn(),
  },
}));

const mockedGetShareImage = vi.mocked(historyApi.getShareImage);

const openShareDialog = async () => {
  fireEvent.click(screen.getByRole('button', { name: '分享' }));
  const dialog = screen.getByRole('dialog', { name: '分享图' });
  await within(dialog).findByRole('img', { name: '分享图预览' });
  return within(dialog).getByRole('group', { name: '分享图版式' });
};

describe('share image default layout per entry point', () => {
  beforeEach(() => {
    window.localStorage.setItem('dsa.uiLanguage', 'zh');
    vi.mocked(historyApi.getMarkdown).mockResolvedValue('# 报告\n\n正文内容');
    mockedGetShareImage.mockReset();
    mockedGetShareImage.mockResolvedValue(new Blob(['jpeg'], { type: 'image/jpeg' }));
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:share-image');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('requests the full report image from the full report drawer panel', async () => {
    render(
      <UiLanguageProvider>
        <ReportMarkdownPanel
          recordId={7}
          stockName="贵州茅台"
          stockCode="600519"
          onRequestClose={() => undefined}
        />
      </UiLanguageProvider>,
    );

    const group = await openShareDialog();

    expect(mockedGetShareImage).toHaveBeenCalledTimes(1);
    expect(mockedGetShareImage).toHaveBeenCalledWith(7, 'full');
    expect(within(group).getByRole('button', { name: '完整报告' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('keeps requesting the decision card from the report page', async () => {
    render(
      <ReportOverview
        meta={{
          id: 8,
          queryId: 'q-8',
          stockCode: '600519',
          stockName: '贵州茅台',
          reportType: 'detailed',
          reportLanguage: 'zh',
          createdAt: '2026-03-21T08:00:00Z',
        }}
        summary={{
          analysisSummary: '趋势维持强势',
          operationAdvice: '继续观察买点',
          trendPrediction: '短线震荡偏强',
          sentimentScore: 78,
        }}
      />,
    );

    const group = await openShareDialog();

    expect(mockedGetShareImage).toHaveBeenCalledTimes(1);
    expect(mockedGetShareImage).toHaveBeenCalledWith(8, 'card');
    expect(within(group).getByRole('button', { name: '决策卡' })).toHaveAttribute('aria-pressed', 'true');
  });
});

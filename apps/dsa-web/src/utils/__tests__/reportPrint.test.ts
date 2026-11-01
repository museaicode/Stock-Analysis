import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  buildReportPrintTitle,
  formatMarketRegionLabel,
  formatPrintDate,
  getMarketReviewRegion,
  printReport,
  restorePendingReportPrint,
  sanitizePrintFileName,
} from '../reportPrint';
import type { AnalysisReport } from '../../types/analysis';

describe('reportPrint utils', () => {
  afterEach(() => {
    restorePendingReportPrint();
    vi.restoreAllMocks();
  });

  it('replaces illegal file name characters', () => {
    expect(sanitizePrintFileName('A/B\\C:D*E?F"G<H>I|J')).toBe('A_B_C_D_E_F_G_H_I_J');
    expect(sanitizePrintFileName('  茅台  600519 \t報告 ')).toBe('茅台_600519_報告');
    expect(sanitizePrintFileName('a\u0001b')).toBe('a_b');
    expect(sanitizePrintFileName('///')).toBe('report');
  });

  it('formats naive server timestamps by their own date', () => {
    expect(formatPrintDate('2026-03-05 23:30:00')).toBe('2026-03-05');
    expect(formatPrintDate('2026-03-05T08:00:00')).toBe('2026-03-05');
    expect(formatPrintDate(undefined, new Date(2026, 0, 2, 10))).toBe('2026-01-02');
    expect(formatPrintDate('not-a-date', new Date(2026, 0, 2, 10))).toBe('2026-01-02');
  });

  it('builds the stock report file name', () => {
    expect(buildReportPrintTitle({
      stockName: '贵州茅台',
      stockCode: '600519',
      createdAt: '2026-10-30 15:01:02',
    })).toBe('贵州茅台_600519_2026-10-30_分析报告');
    expect(buildReportPrintTitle({
      stockName: 'A/B: "Corp"',
      stockCode: 'AB*C',
      createdAt: '2026-10-30T15:01:02',
    })).toBe('A_B_Corp_AB_C_2026-10-30_分析报告');
    expect(buildReportPrintTitle({ stockName: '', stockCode: 'AAPL', createdAt: '2026-10-30' }))
      .toBe('AAPL_2026-10-30_分析报告');
  });

  it('builds the market review file name', () => {
    expect(buildReportPrintTitle({
      reportType: 'market_review',
      stockName: '大盘复盘',
      stockCode: 'MARKET',
      createdAt: '2026-10-30 15:01:02',
      marketRegion: 'cn,us',
    })).toBe('A股+美股_大盘复盘_2026-10-30');
    expect(formatMarketRegionLabel('both')).toBe('全部市场');
    expect(formatMarketRegionLabel('hk')).toBe('港股');
    expect(formatMarketRegionLabel(undefined)).toBe('市场');
  });

  it('reads the market review region from the context snapshot', () => {
    const report = {
      details: { contextSnapshot: { marketReviewPayload: { region: 'hk' } } },
    } as unknown as AnalysisReport;
    expect(getMarketReviewRegion(report)).toBe('hk');
    expect(getMarketReviewRegion({
      details: { contextSnapshot: { marketReviewRegion: 'cn' } },
    } as unknown as AnalysisReport)).toBe('cn');
    expect(getMarketReviewRegion(null)).toBeNull();
  });

  it('sets the title and expands collapsed blocks while printing, then restores on afterprint', () => {
    document.title = 'DSA';
    const root = document.createElement('div');
    root.innerHTML = '<details><summary>more</summary><p>hidden</p></details><details open><summary>x</summary></details>';
    document.body.appendChild(root);
    const [collapsed, opened] = Array.from(root.querySelectorAll('details'));

    let titleDuringPrint = '';
    let collapsedOpenDuringPrint = false;
    const print = vi.spyOn(window, 'print').mockImplementation(() => {
      titleDuringPrint = document.title;
      collapsedOpenDuringPrint = collapsed.open;
    });

    printReport('茅台/600519:2026-10-30_分析报告', root);

    expect(print).toHaveBeenCalledTimes(1);
    expect(titleDuringPrint).toBe('茅台_600519_2026-10-30_分析报告');
    expect(collapsedOpenDuringPrint).toBe(true);
    expect(document.title).toBe('茅台_600519_2026-10-30_分析报告');

    window.dispatchEvent(new Event('afterprint'));

    expect(document.title).toBe('DSA');
    expect(collapsed.open).toBe(false);
    expect(opened.open).toBe(true);
    root.remove();
  });
});

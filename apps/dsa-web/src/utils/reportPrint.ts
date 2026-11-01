/**
 * Browser-print based "Export PDF" helpers for the full analysis report.
 *
 * The browser print dialog uses `document.title` as the default PDF file name,
 * so we temporarily swap the title before `window.print()` and restore it on
 * `afterprint`. No backend endpoint or extra dependency is involved.
 */

import type { AnalysisReport } from '../types/analysis';

/** Marker attribute on the element whose content should be printed. */
export const REPORT_PRINT_ROOT_ATTR = 'data-report-print-root';

const ILLEGAL_FILE_NAME_CHARS = /[/\\:*?"<>|]/g;
const MAX_FILE_NAME_LENGTH = 120;

/** Replace characters that are illegal in file names and tidy the result. */
export function sanitizePrintFileName(name: string): string {
  const withoutControlChars = Array.from(name, (ch) => {
    const code = ch.charCodeAt(0);
    return code < 32 || code === 127 ? '_' : ch;
  }).join('');
  const cleaned = withoutControlChars
    .replace(ILLEGAL_FILE_NAME_CHARS, '_')
    .replace(/\s+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^[_.]+|[_.]+$/g, '');
  return cleaned.slice(0, MAX_FILE_NAME_LENGTH) || 'report';
}

const NAIVE_DATE_PREFIX = /^(\d{4}-\d{2}-\d{2})(?:[T\s]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$/;

/**
 * Format the analysis timestamp as `YYYY-MM-DD`. Naive server timestamps keep their
 * own date; zoned timestamps use the viewer's local date; invalid/missing → today.
 */
export function formatPrintDate(value?: string | null, now: Date = new Date()): string {
  const trimmed = (value || '').trim();
  const naive = NAIVE_DATE_PREFIX.exec(trimmed);
  if (naive) {
    return naive[1];
  }
  const parsed = trimmed ? new Date(trimmed) : null;
  const date = parsed && !Number.isNaN(parsed.getTime()) ? parsed : now;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

const MARKET_REGION_LABELS: Record<string, string> = {
  cn: 'A股',
  hk: '港股',
  us: '美股',
  jp: '日股',
  kr: '韩股',
};
const MARKET_REGION_ORDER = ['cn', 'hk', 'us', 'jp', 'kr'];

/** Human-readable market label for a market-review region string (`cn`, `cn,us`, `both`). */
export function formatMarketRegionLabel(region?: string | null): string {
  const normalized = (region || '').trim().toLowerCase();
  if (!normalized) {
    return '市场';
  }
  if (normalized === 'both' || normalized === 'all') {
    return '全部市场';
  }
  const tokens = normalized.split(/[\s,+|/]+/).filter(Boolean);
  const known = MARKET_REGION_ORDER.filter((key) => tokens.includes(key));
  if (known.length === 0) {
    return normalized.toUpperCase();
  }
  return known.map((key) => MARKET_REGION_LABELS[key]).join('+');
}

/** Read the market-review region recorded in a history report's context snapshot. */
export function getMarketReviewRegion(report?: AnalysisReport | null): string | null {
  const snapshot = report?.details?.contextSnapshot;
  if (!snapshot) {
    return null;
  }
  const direct = snapshot.marketReviewRegion;
  if (typeof direct === 'string' && direct.trim()) {
    return direct.trim();
  }
  const payloadRegion = snapshot.marketReviewPayload?.region;
  return typeof payloadRegion === 'string' && payloadRegion.trim() ? payloadRegion.trim() : null;
}

export interface ReportPrintTitleInput {
  reportType?: string;
  stockName?: string;
  stockCode?: string;
  createdAt?: string | null;
  marketRegion?: string | null;
}

/**
 * Build the suggested PDF file name:
 * - stock report: `{股票名称}_{代码}_{分析日期}_分析报告`
 * - market review: `{市场}_大盘复盘_{日期}`
 */
export function buildReportPrintTitle(input: ReportPrintTitleInput, now: Date = new Date()): string {
  const date = formatPrintDate(input.createdAt, now);
  if (input.reportType === 'market_review') {
    return sanitizePrintFileName(`${formatMarketRegionLabel(input.marketRegion)}_大盘复盘_${date}`);
  }
  const code = (input.stockCode || '').trim();
  const name = (input.stockName || '').trim() || code;
  const parts = [name, code && code !== name ? code : '', date, '分析报告'].filter(Boolean);
  return sanitizePrintFileName(parts.join('_'));
}

type PendingRestore = () => void;
let pendingRestore: PendingRestore | null = null;

/** Restore title / collapsed blocks left over from a print that never fired `afterprint`. */
export function restorePendingReportPrint(): void {
  const restore = pendingRestore;
  pendingRestore = null;
  restore?.();
}

/**
 * Temporarily set the document title, expand collapsed `<details>` blocks in the
 * print root, and open the browser print dialog. Everything is restored on `afterprint`.
 */
export function printReport(title: string, root?: ParentNode | null): void {
  restorePendingReportPrint();

  const originalTitle = document.title;
  const collapsed = Array.from(root?.querySelectorAll('details:not([open])') ?? []) as HTMLDetailsElement[];
  collapsed.forEach((element) => {
    element.open = true;
  });
  document.title = sanitizePrintFileName(title);

  const restore = () => {
    window.removeEventListener('afterprint', handleAfterPrint);
    document.title = originalTitle;
    collapsed.forEach((element) => {
      element.open = false;
    });
  };
  const handleAfterPrint = () => {
    if (pendingRestore === restore) {
      pendingRestore = null;
    }
    restore();
  };

  pendingRestore = restore;
  window.addEventListener('afterprint', handleAfterPrint);
  window.print();
}

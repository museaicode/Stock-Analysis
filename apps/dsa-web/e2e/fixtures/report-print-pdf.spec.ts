import { expect, test, type Page } from '@playwright/test';

// Mount the real ReportMarkdownDrawer (production CSS + localization) on top of a
// fake app shell (sidebar, top nav, page content, toast). Only the markdown API
// response is fixed, so no backend is needed.
const LONG_SECTIONS = Array.from({ length: 40 }, (_, index) => (
  `### 第 ${index + 1} 节 分析要点\n\n这是用于验证长报告分页的段落 ${index + 1}，内容包括趋势、量能、资金与风险评估。`
)).join('\n\n');

const REPORT_MARKDOWN = `# 贵州茅台 (600519) 决策报告

> 分析时间：2026-10-30 15:01

## 核心结论

| 指标 | 数值 | 指标 | 数值 | 指标 | 数值 | 指标 | 数值 | 备注说明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 评分 | 72 | 涨跌幅 | +1.25% | 量比 | 1.36 | 换手率 | 0.42% | 这是一列非常长的备注，用于验证超宽表格在 A4 打印时会自适应宽度而不会被截断 |

\`\`\`text
支撑位 1650 / 压力位 1780
\`\`\`

${LONG_SECTIONS}

## 风险提示

REPORT_TAIL_MARKER：AI 生成，仅供参考，不构成投资建议。
`;

const mountReportDrawer = async (page: Page, theme: 'light' | 'dark') => {
  await page.route('**/api/v1/history/21/markdown', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ content: REPORT_MARKDOWN }),
  }));
  await page.route('**/src/main.tsx', (route) => route.fulfill({
    contentType: 'application/javascript',
    body: `
      import React from '/node_modules/.vite/deps/react.js';
      import ReactDOMClient from '/node_modules/.vite/deps/react-dom_client.js';
      import '/src/index.css';
      import { ReportMarkdownDrawer } from '/src/components/report/ReportMarkdownDrawer.tsx';
      const h = React.createElement;
      if (${JSON.stringify(theme)} === 'dark') document.documentElement.classList.add('dark');
      ReactDOMClient.createRoot(document.getElementById('root')).render(
        h('div', {className: 'flex min-h-screen bg-background text-foreground'},
          h('aside', {'data-testid': 'fixture-sidebar', className: 'w-56 border-r border-border p-4'}, '侧边栏'),
          h('div', {className: 'flex-1'},
            h('header', {'data-testid': 'fixture-topnav', className: 'border-b border-border p-4'}, '顶部导航'),
            h('main', {'data-testid': 'fixture-page', className: 'p-6'}, '底层页面内容：报告详情页'),
            h('div', {'data-testid': 'fixture-toast', role: 'status', className: 'fixed bottom-4 right-4 rounded bg-card p-3'}, '已复制'),
          ),
          h(ReportMarkdownDrawer, {
            recordId: 21,
            stockName: '贵州茅台',
            stockCode: '600519',
            createdAt: '2026-10-30 15:01:02',
            reportLanguage: 'zh',
            onClose: () => {},
          }),
        )
      );
    `,
  }));
};

const openReport = async (page: Page, theme: 'light' | 'dark') => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dsa.uiLanguage', 'zh');
  });
  await mountReportDrawer(page, theme);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/');
  const drawer = page.getByRole('dialog');
  await expect(drawer.getByText('REPORT_TAIL_MARKER', { exact: false })).toBeAttached();
  await expect(drawer.getByRole('button', { name: '导出 PDF' })).toBeEnabled();

  // The current Markdown report has no collapsible blocks; inject one to verify the
  // generic "expand collapsed blocks when printing" contract.
  await page.evaluate(() => {
    const body = document.querySelector('[data-testid="report-markdown-body"]');
    const details = document.createElement('details');
    details.setAttribute('data-testid', 'fixture-collapsed');
    details.innerHTML = '<summary>展开更多</summary><p data-testid="fixture-collapsed-content">折叠区块内容</p>';
    body?.appendChild(details);
  });
  return drawer;
};

const assertPrintLayout = async (page: Page) => {
  for (const testId of ['fixture-sidebar', 'fixture-topnav', 'fixture-page', 'fixture-toast']) {
    await expect(page.getByTestId(testId)).toBeHidden();
  }
  // Every button (export, share, copy, drawer close, bottom close bar) is hidden.
  const visibleButtons = await page.locator('button').evaluateAll(
    (nodes) => nodes.filter((node) => getComputedStyle(node).display !== 'none' && (node as HTMLElement).offsetParent !== null).length,
  );
  expect(visibleButtons).toBe(0);

  const layout = await page.evaluate(() => {
    const root = document.querySelector('[data-report-print-root]') as HTMLElement;
    const dialog = document.querySelector('[role="dialog"]') as HTMLElement;
    const presentation = document.querySelector('[role="presentation"]') as HTMLElement;
    const backdrop = presentation?.firstElementChild as HTMLElement;
    const scroller = root.parentElement as HTMLElement;
    const tail = Array.from(root.querySelectorAll('p')).find((p) => p.textContent?.includes('REPORT_TAIL_MARKER')) as HTMLElement;
    const table = root.querySelector('table') as HTMLElement;
    return {
      bodyBg: getComputedStyle(document.body).backgroundColor,
      rootBg: getComputedStyle(root).backgroundColor,
      rootColor: getComputedStyle(root).color,
      backdropDisplay: getComputedStyle(backdrop).display,
      presentationPosition: getComputedStyle(presentation).position,
      dialogOverflow: getComputedStyle(dialog).overflow,
      scrollerOverflow: getComputedStyle(scroller).overflowY,
      scrollerClipped: scroller.scrollHeight > scroller.clientHeight + 1,
      tailVisible: tail.getBoundingClientRect().height > 0,
      tailBottom: tail.getBoundingClientRect().bottom,
      rootHeight: root.getBoundingClientRect().height,
      tableDisplay: getComputedStyle(table).display,
      tableFits: table.getBoundingClientRect().width <= root.getBoundingClientRect().width + 1,
      printOnlyDisplay: getComputedStyle(document.querySelector('[data-testid="report-print-meta"]') as HTMLElement).display,
      disclaimerDisplay: getComputedStyle(document.querySelector('[data-testid="report-print-disclaimer"]') as HTMLElement).display,
      collapsedContentVisible: (document.querySelector('[data-testid="fixture-collapsed-content"]') as HTMLElement).getBoundingClientRect().height > 0,
      colorAdjust: getComputedStyle(root).getPropertyValue('print-color-adjust'),
      summaryListStyle: getComputedStyle(root.querySelector('summary') as HTMLElement).listStyleType,
    };
  });

  expect(layout.bodyBg).toBe('rgb(255, 255, 255)');
  expect(layout.rootBg).toBe('rgb(255, 255, 255)');
  // Dark text (each RGB channel well below mid-grey).
  const [r, g, b] = layout.rootColor.match(/\d+/g)!.map(Number);
  expect(Math.max(r, g, b)).toBeLessThan(80);
  expect(layout.backdropDisplay).toBe('none');
  expect(layout.presentationPosition).toBe('static');
  expect(layout.dialogOverflow).toBe('visible');
  expect(layout.scrollerOverflow).toBe('visible');
  expect(layout.scrollerClipped).toBe(false);
  expect(layout.tailVisible).toBe(true);
  // The tail of a long report lies far below one viewport: it flows to later pages.
  expect(layout.rootHeight).toBeGreaterThan(900);
  expect(layout.tableDisplay).toBe('table');
  expect(layout.tableFits).toBe(true);
  expect(layout.printOnlyDisplay).toBe('block');
  expect(layout.disclaimerDisplay).toBe('block');
  expect(layout.collapsedContentVisible).toBe(true);
  expect(layout.colorAdjust).toBe('exact');
  expect(layout.summaryListStyle).toBe('none');
};

for (const theme of ['light', 'dark'] as const) {
  test(`print mode outputs only the full report in a light palette (${theme} theme)`, async ({ page }, testInfo) => {
    const drawer = await openReport(page, theme);

    // Clicking "导出 PDF" calls window.print() with the report file name as the title.
    await page.evaluate(() => {
      (window as unknown as { __printTitles: string[] }).__printTitles = [];
      window.print = () => {
        (window as unknown as { __printTitles: string[] }).__printTitles.push(document.title);
      };
    });
    const originalTitle = await page.title();
    await drawer.getByRole('button', { name: '导出 PDF' }).click();
    expect(await page.evaluate(() => (window as unknown as { __printTitles: string[] }).__printTitles))
      .toEqual(['贵州茅台_600519_2026-10-30_分析报告']);
    // Collapsed blocks are opened for the print job.
    expect(await page.getByTestId('fixture-collapsed').evaluate((node) => (node as HTMLDetailsElement).open)).toBe(true);

    await page.emulateMedia({ media: 'print' });
    await assertPrintLayout(page);

    const shot = testInfo.outputPath(`report-print-${theme}.png`);
    await page.screenshot({ path: shot, fullPage: true, animations: 'disabled' });
    await testInfo.attach(`report-print-${theme}`, { path: shot, contentType: 'image/png' });

    if (theme === 'dark') {
      const pdfPath = testInfo.outputPath('report-print-sample.pdf');
      await page.pdf({ path: pdfPath, format: 'A4', preferCSSPageSize: true, printBackground: true });
      await testInfo.attach('report-print-sample', { path: pdfPath, contentType: 'application/pdf' });
    }

    // afterprint restores the title, the collapsed block and the on-screen drawer.
    await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
    await page.emulateMedia({ media: 'screen' });
    expect(await page.title()).toBe(originalTitle);
    expect(await page.getByTestId('fixture-collapsed').evaluate((node) => (node as HTMLDetailsElement).open)).toBe(false);
    await expect(page.getByTestId('fixture-page')).toBeVisible();
    await expect(drawer.getByRole('button', { name: '导出 PDF' })).toBeVisible();
    const scrollerOverflow = await page.evaluate(() => (
      getComputedStyle((document.querySelector('[data-report-print-root]') as HTMLElement).parentElement as HTMLElement).overflowY
    ));
    expect(scrollerOverflow).toBe('auto');
  });
}

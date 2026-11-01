import { expect, test, type Page } from '@playwright/test';

// Mount the real ReportMarkdownDrawer (production CSS + localization) and open the
// share dialog from inside it. Only the markdown and share-image API responses are
// fixed, so no backend or image renderer is needed.
const REPORT_MARKDOWN = `# 贵州茅台 (600519) 决策报告

## 核心结论

趋势偏强，等待回踩支撑后分批执行。

## 风险提示

AI 生成，仅供参考，不构成投资建议。
`;

const posterPng = async (page: Page): Promise<Buffer> => {
  const poster = await page.context().newPage();
  await poster.setViewportSize({ width: 540, height: 720 });
  await poster.setContent(`
    <body style="margin:0;font-family:sans-serif;background:linear-gradient(180deg,#eef4ff,#ffffff)">
      <div style="padding:40px">
        <div style="font-size:36px;font-weight:700">贵州茅台 600519</div>
        <div style="margin-top:16px;padding:24px;border-radius:16px;background:#fff;border:1px solid #dbe4f3">
          <div style="font-size:22px;color:#16a34a">买入 · 评分 72</div>
        </div>
      </div>
    </body>`);
  const body = await poster.screenshot({ type: 'png' });
  await poster.close();
  return body;
};

const mountReportDrawer = async (page: Page) => {
  const image = await posterPng(page);
  await page.route('**/api/v1/history/21/markdown', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ content: REPORT_MARKDOWN }),
  }));
  await page.route('**/api/v1/history/21/share-image', (route) => route.fulfill({
    status: 200,
    contentType: 'image/png',
    body: image,
  }));
  await page.route('**/src/main.tsx', (route) => route.fulfill({
    contentType: 'application/javascript',
    body: `
      import React from '/node_modules/.vite/deps/react.js';
      import ReactDOMClient from '/node_modules/.vite/deps/react-dom_client.js';
      import '/src/index.css';
      import { ReportMarkdownDrawer } from '/src/components/report/ReportMarkdownDrawer.tsx';
      const h = React.createElement;
      function App() {
        const [open, setOpen] = React.useState(true);
        return h('div', {className: 'min-h-screen bg-background p-6 text-foreground'},
          h('main', {'data-testid': 'fixture-page'}, '底层页面内容：报告详情页'),
          open ? h(ReportMarkdownDrawer, {
            recordId: 21,
            stockName: '贵州茅台',
            stockCode: '600519',
            createdAt: '2026-10-30 15:01:02',
            reportLanguage: 'zh',
            onClose: () => setOpen(false),
          }) : null,
        );
      }
      ReactDOMClient.createRoot(document.getElementById('root')).render(h(App));
    `,
  }));
};

/** The element hit-tested at the centre of the share dialog belongs to the dialog. */
const dialogCentreIsOnTop = (page: Page) => page.getByRole('dialog', { name: '分享图' }).evaluate((node) => {
  const rect = node.getBoundingClientRect();
  const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
  return Boolean(hit && node.contains(hit));
});

test('share dialog opened from the full report drawer sits on top and closes layer by layer', async ({ page }, testInfo) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('dsa.uiLanguage', 'zh');
  });
  await mountReportDrawer(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/');

  const drawer = page.getByRole('dialog').filter({ has: page.getByRole('button', { name: '导出 PDF' }) });
  await expect(drawer.getByText('趋势偏强', { exact: false })).toBeVisible();

  const shareButton = drawer.getByRole('button', { name: '分享' });
  await shareButton.click();
  const shareDialog = page.getByRole('dialog', { name: '分享图' });
  await expect(shareDialog).toBeVisible();
  await expect(shareDialog.getByRole('img', { name: '分享图预览' })).toBeVisible();
  // Portaled to <body>, outside the drawer, and hit-testing its centre lands on it.
  expect(await shareDialog.evaluate((node) => node.parentElement?.parentElement === document.body)).toBe(true);
  expect(await dialogCentreIsOnTop(page)).toBe(true);
  // Focus moved into the share dialog.
  expect(await shareDialog.evaluate((node) => node.contains(document.activeElement))).toBe(true);

  const shot = testInfo.outputPath('share-dialog-in-report-drawer-1280.png');
  await page.screenshot({ path: shot, animations: 'disabled' });
  await testInfo.attach('share-dialog-in-report-drawer-1280', { path: shot, contentType: 'image/png' });

  // Print mode keeps the share dialog hidden; only the report prints.
  await page.emulateMedia({ media: 'print' });
  await expect(shareDialog).toBeHidden();
  await page.emulateMedia({ media: 'screen' });
  await expect(shareDialog).toBeVisible();

  // Clicking inside the dialog (image, download) keeps both layers open.
  await shareDialog.getByRole('img', { name: '分享图预览' }).click();
  const downloadPromise = page.waitForEvent('download');
  await shareDialog.getByRole('button', { name: '下载图片' }).click();
  // Headless Chromium reports non-ASCII `a.download` names as "download", so only
  // the download itself is asserted here (file naming is covered by unit tests).
  await downloadPromise;
  await expect(shareDialog).toBeVisible();
  await expect(drawer).toBeVisible();

  // Escape closes the share dialog only; focus returns to the drawer's share button.
  await page.keyboard.press('Escape');
  await expect(shareDialog).toHaveCount(0);
  await expect(drawer).toBeVisible();
  await expect(shareButton).toBeFocused();

  // Clicking the share dialog's own backdrop also closes only that dialog.
  await shareButton.click();
  await expect(shareDialog).toBeVisible();
  await page.mouse.click(20, 20);
  await expect(shareDialog).toHaveCount(0);
  await expect(drawer).toBeVisible();

  // A second Escape closes the drawer.
  await shareButton.focus();
  await page.keyboard.press('Escape');
  await expect(drawer).toHaveCount(0);
  await expect(page.getByTestId('fixture-page')).toBeVisible();
});

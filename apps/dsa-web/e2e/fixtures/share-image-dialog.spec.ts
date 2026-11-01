import { expect, test, type Page } from '@playwright/test';

// Mount the real ShareImageButton with production CSS and localization. Only the
// share-image API response is fixed, so no backend or image renderer is needed.
const mountShareButton = async (page: Page) => {
  await page.route('**/src/main.tsx', (route) => route.fulfill({
    contentType: 'application/javascript',
    body: `
      import React from '/node_modules/.vite/deps/react.js';
      import ReactDOMClient from '/node_modules/.vite/deps/react-dom_client.js';
      import '/src/index.css';
      import { ShareImageButton } from '/src/components/report/ShareImageButton.tsx';
      ReactDOMClient.createRoot(document.getElementById('root')).render(
        React.createElement('main', {className: 'min-h-screen bg-background p-4 text-foreground'},
          React.createElement('div', {className: 'flex items-center justify-between gap-3 rounded-xl border border-border/60 p-4'},
            React.createElement('h1', {className: 'text-lg font-semibold'}, '贵州茅台 (600519)'),
            React.createElement(ShareImageButton, {recordId: 21, reportTitle: 'Moutai-600519', reportLanguage: 'zh'})
          )
        )
      );
    `,
  }));
};

const posterJpeg = async (page: Page): Promise<Buffer> => {
  const poster = await page.context().newPage();
  await poster.setViewportSize({ width: 540, height: 960 });
  await poster.setContent(`
    <body style="margin:0;font-family:sans-serif;background:linear-gradient(180deg,#eef4ff,#ffffff)">
      <div style="padding:40px">
        <div style="font-size:44px;font-weight:800;color:#0b1f44">DSA</div>
        <div style="margin-top:24px;font-size:36px;font-weight:700">贵州茅台 600519</div>
        <div style="margin-top:16px;padding:24px;border-radius:16px;background:#fff;border:1px solid #dbe4f3">
          <div style="font-size:22px;color:#16a34a">买入 · 评分 72</div>
          <p style="font-size:18px;line-height:1.6;color:#334155">趋势偏强，等待回踩支撑后分批执行。</p>
        </div>
      </div>
    </body>`);
  const body = await poster.screenshot({ type: 'jpeg', quality: 85 });
  await poster.close();
  return body;
};

test('share opens a loading dialog immediately, then offers actions from a fresh click', async ({ page }, testInfo) => {
  const body = await posterJpeg(page);
  let releaseImage: (() => void) | undefined;
  let imageReleased = Promise.resolve();
  await page.route('**/api/v1/history/21/share-image', async (route) => {
    await imageReleased;
    await route.fulfill({
      status: 200,
      contentType: 'image/jpeg',
      headers: { 'Content-Disposition': 'attachment; filename="dsa-report-21.jpg"' },
      body,
    });
  });
  await mountShareButton(page);

  for (const width of [1280, 390]) {
    imageReleased = new Promise<void>((resolve) => {
      releaseImage = resolve;
    });
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');

    await page.getByRole('button', { name: '分享' }).click();
    const dialog = page.getByRole('dialog', { name: '分享图' });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('status')).toHaveText('正在生成分享图...');
    const loadingShot = testInfo.outputPath(`share-dialog-loading-${width}.png`);
    await page.screenshot({ path: loadingShot, animations: 'disabled' });
    await testInfo.attach(`share-dialog-loading-${width}`, { path: loadingShot, contentType: 'image/png' });

    releaseImage?.();
    await expect(dialog.getByRole('img', { name: '分享图预览' })).toBeVisible();
    await expect(dialog.getByRole('button', { name: '下载图片' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const readyShot = testInfo.outputPath(`share-dialog-ready-${width}.png`);
    await page.screenshot({ path: readyShot, animations: 'disabled' });
    await testInfo.attach(`share-dialog-ready-${width}`, { path: readyShot, contentType: 'image/png' });
  }

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('dialog', { name: '分享图' }).getByRole('button', { name: '下载图片' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('Moutai-600519-21.jpg');
});

test('share-image API failure closes the dialog and shows a toast', async ({ page }, testInfo) => {
  await page.route('**/api/v1/history/21/share-image', (route) => route.fulfill({
    status: 503,
    contentType: 'application/json',
    body: JSON.stringify({
      detail: {
        error: 'share_image_unavailable',
        message: '分享图片生成失败，请检查 wkhtmltoimage 转图工具是否已安装并可用',
      },
    }),
  }));
  await mountShareButton(page);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto('/');

  await page.getByRole('button', { name: '分享' }).click();
  const alert = page.getByRole('alert');
  await expect(alert).toContainText('生成分享图失败');
  await expect(alert).toContainText('wkhtmltoimage');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  const errorShot = testInfo.outputPath('share-dialog-error-toast-1280.png');
  await page.screenshot({ path: errorShot, animations: 'disabled' });
  await testInfo.attach('share-dialog-error-toast-1280', { path: errorShot, contentType: 'image/png' });
});

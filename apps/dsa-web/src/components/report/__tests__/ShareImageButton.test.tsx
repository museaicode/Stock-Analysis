import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { historyApi } from '../../../api/history';
import { ShareImageButton } from '../ShareImageButton';

vi.mock('../../../api/history', () => ({
  historyApi: {
    getShareImage: vi.fn(),
  },
}));

const mockedGetShareImage = vi.mocked(historyApi.getShareImage);

class FakeClipboardItem {
  readonly items: Record<string, Blob | Promise<Blob>>;

  constructor(items: Record<string, Blob | Promise<Blob>>) {
    this.items = items;
  }
}

const setNavigatorValue = (key: 'share' | 'canShare' | 'clipboard', value: unknown) => {
  Object.defineProperty(navigator, key, { configurable: true, value });
};

const enableClipboard = (write: ReturnType<typeof vi.fn>) => {
  setNavigatorValue('clipboard', { write });
  Object.defineProperty(window, 'ClipboardItem', { configurable: true, writable: true, value: FakeClipboardItem });
};

const enableFileShare = (share: ReturnType<typeof vi.fn>) => {
  setNavigatorValue('share', share);
  setNavigatorValue('canShare', (data: ShareData) => Boolean(data.files?.length));
};

const jpegBlob = () => new Blob(['jpeg'], { type: 'image/jpeg' });
const pngBlob = () => new Blob(['png'], { type: 'image/png' });

const renderButton = (recordId = 17, reportTitle = '中钨高新-000657') => render(
  <ShareImageButton recordId={recordId} reportTitle={reportTitle} reportLanguage="zh" />,
);

const openDialogAndWaitForPreview = async () => {
  fireEvent.click(screen.getByRole('button', { name: '分享' }));
  const dialog = screen.getByRole('dialog', { name: '分享图' });
  await within(dialog).findByRole('img', { name: '分享图预览' });
  return dialog;
};

describe('ShareImageButton', () => {
  beforeEach(() => {
    vi.useRealTimers();
    mockedGetShareImage.mockReset();
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:share-image');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    Object.defineProperty(window, 'dsaDesktop', { configurable: true, value: undefined });
    Object.defineProperty(window, 'ClipboardItem', { configurable: true, writable: true, value: undefined });
    setNavigatorValue('share', undefined);
    setNavigatorValue('canShare', undefined);
    setNavigatorValue('clipboard', undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('opens the dialog with a loading state immediately on the first click', () => {
    mockedGetShareImage.mockReturnValue(new Promise(() => undefined));
    renderButton();

    expect(mockedGetShareImage).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '分享' }));

    const dialog = screen.getByRole('dialog', { name: '分享图' });
    expect(within(dialog).getByRole('status')).toHaveTextContent('正在生成分享图...');
    expect(within(dialog).queryByRole('button', { name: '下载图片' })).not.toBeInTheDocument();
    expect(mockedGetShareImage).toHaveBeenCalledWith(17, 'card');
  });

  it('shows the preview and every supported action once the image is loaded', async () => {
    let resolveImage: ((blob: Blob) => void) | undefined;
    mockedGetShareImage.mockReturnValue(new Promise((resolve) => {
      resolveImage = resolve;
    }));
    enableClipboard(vi.fn().mockResolvedValue(undefined));
    enableFileShare(vi.fn().mockResolvedValue(undefined));
    renderButton();

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
    const dialog = screen.getByRole('dialog', { name: '分享图' });
    expect(within(dialog).getByRole('status')).toBeInTheDocument();

    await act(async () => {
      resolveImage?.(jpegBlob());
    });

    expect(within(dialog).getByRole('img', { name: '分享图预览' })).toHaveAttribute('src', 'blob:share-image');
    expect(within(dialog).queryByRole('status')).not.toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '下载图片' })).toBeEnabled();
    expect(within(dialog).getByRole('button', { name: '复制图片' })).toBeEnabled();
    expect(within(dialog).getByRole('button', { name: '系统分享' })).toBeEnabled();
  });

  it('only offers download when clipboard images and file sharing are unavailable', async () => {
    mockedGetShareImage.mockResolvedValue(jpegBlob());
    setNavigatorValue('share', vi.fn());
    setNavigatorValue('canShare', () => false);
    renderButton();

    const dialog = await openDialogAndWaitForPreview();

    expect(within(dialog).getByRole('button', { name: '下载图片' })).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: '复制图片' })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: '系统分享' })).not.toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: '下载图片' }));
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
  });

  it('downloads with an extension matching the returned image format', async () => {
    mockedGetShareImage.mockResolvedValue(jpegBlob());
    const anchors: HTMLAnchorElement[] = [];
    vi.mocked(HTMLAnchorElement.prototype.click).mockImplementation(function (this: HTMLAnchorElement) {
      anchors.push(this);
    });
    renderButton(17, '中钨高新-000657');

    const dialog = await openDialogAndWaitForPreview();
    fireEvent.click(within(dialog).getByRole('button', { name: '下载图片' }));

    expect(anchors[0].download).toBe('中钨高新-000657-17_card.jpg');
  });

  it('invokes navigator.share synchronously from the dialog click with the image file', async () => {
    const nativeShare = vi.fn().mockResolvedValue(undefined);
    enableFileShare(nativeShare);
    mockedGetShareImage.mockResolvedValue(jpegBlob());
    renderButton(18, 'A股市场复盘');

    const dialog = await openDialogAndWaitForPreview();
    expect(nativeShare).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole('button', { name: '系统分享' }));

    // 同步调用：无需等待任何异步步骤
    expect(nativeShare).toHaveBeenCalledTimes(1);
    const payload = nativeShare.mock.calls[0][0];
    expect(payload.title).toBe('A股市场复盘');
    expect(payload.files[0].name).toBe('A股市场复盘-18_card.jpg');
    expect(payload.files[0].type).toBe('image/jpeg');
    expect(mockedGetShareImage).toHaveBeenCalledTimes(1);
  });

  it('shows a toast when system sharing is rejected with NotAllowedError', async () => {
    enableFileShare(vi.fn().mockRejectedValue(new DOMException('activation expired', 'NotAllowedError')));
    mockedGetShareImage.mockResolvedValue(jpegBlob());
    renderButton();

    const dialog = await openDialogAndWaitForPreview();
    fireEvent.click(within(dialog).getByRole('button', { name: '系统分享' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('系统分享失败');
    expect(alert).toHaveTextContent('浏览器拒绝了本次操作');
  });

  it('stays silent when the user cancels the system share sheet', async () => {
    const nativeShare = vi.fn().mockRejectedValue(new DOMException('cancelled', 'AbortError'));
    enableFileShare(nativeShare);
    mockedGetShareImage.mockResolvedValue(jpegBlob());
    renderButton();

    const dialog = await openDialogAndWaitForPreview();
    await act(async () => {
      fireEvent.click(within(dialog).getByRole('button', { name: '系统分享' }));
      await Promise.resolve();
    });

    expect(nativeShare).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: '分享图' })).toBeInTheDocument();
  });

  it('writes a PNG ClipboardItem synchronously and confirms with a toast', async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    enableClipboard(write);
    const image = pngBlob();
    mockedGetShareImage.mockResolvedValue(image);
    renderButton();

    const dialog = await openDialogAndWaitForPreview();
    fireEvent.click(within(dialog).getByRole('button', { name: '复制图片' }));

    expect(write).toHaveBeenCalledTimes(1);
    const [item] = write.mock.calls[0][0] as FakeClipboardItem[];
    await expect(item.items['image/png']).resolves.toBe(image);
    expect(await screen.findByRole('alert')).toHaveTextContent('图片已复制到剪贴板');
  });

  it('shows a toast when clipboard.write is rejected with NotAllowedError', async () => {
    enableClipboard(vi.fn().mockRejectedValue(new DOMException('denied', 'NotAllowedError')));
    mockedGetShareImage.mockResolvedValue(pngBlob());
    renderButton();

    const dialog = await openDialogAndWaitForPreview();
    fireEvent.click(within(dialog).getByRole('button', { name: '复制图片' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('复制图片失败');
    expect(alert).toHaveTextContent('浏览器拒绝了本次操作');
  });

  it('closes the dialog and shows a toast when the share-image request fails', async () => {
    mockedGetShareImage.mockRejectedValue(new Error('分享图片生成失败，请检查 wkhtmltoimage 转图工具是否已安装并可用'));
    renderButton(19);

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
    expect(screen.getByRole('dialog', { name: '分享图' })).toBeInTheDocument();

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('生成分享图失败');
    expect(alert).toHaveTextContent('wkhtmltoimage');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(mockedGetShareImage).toHaveBeenCalledWith(19, 'card');

    // 失败后可重试
    mockedGetShareImage.mockResolvedValue(pngBlob());
    await openDialogAndWaitForPreview();
    expect(mockedGetShareImage).toHaveBeenCalledTimes(2);
  });

  it('reuses the loaded image when the dialog is reopened', async () => {
    mockedGetShareImage.mockResolvedValue(jpegBlob());
    renderButton();

    const dialog = await openDialogAndWaitForPreview();
    fireEvent.click(within(dialog).getByRole('button', { name: '关闭' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
    expect(screen.getByRole('img', { name: '分享图预览' })).toBeInTheDocument();
    expect(mockedGetShareImage).toHaveBeenCalledTimes(1);
  });

  it('keeps generating after the dialog is closed mid-load and reuses the result', async () => {
    let resolveImage: ((blob: Blob) => void) | undefined;
    mockedGetShareImage.mockReturnValue(new Promise((resolve) => {
      resolveImage = resolve;
    }));
    renderButton();

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: '分享图' })).getByRole('button', { name: '关闭' }));
    fireEvent.click(screen.getByRole('button', { name: '分享' }));
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(mockedGetShareImage).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveImage?.(jpegBlob());
    });

    expect(screen.getByRole('img', { name: '分享图预览' })).toBeInTheDocument();
    expect(mockedGetShareImage).toHaveBeenCalledTimes(1);
  });

  it('loads a fresh image after switching to another record', async () => {
    mockedGetShareImage.mockResolvedValueOnce(jpegBlob());
    const { rerender } = renderButton(21, '报告A');

    await openDialogAndWaitForPreview();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    mockedGetShareImage.mockReturnValueOnce(new Promise(() => undefined));
    rerender(<ShareImageButton recordId={22} reportTitle="报告B" reportLanguage="zh" />);

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(mockedGetShareImage).toHaveBeenLastCalledWith(22, 'card');
  });

  it('keeps the button hidden for an older desktop bridge without image rendering', () => {
    Object.defineProperty(window, 'dsaDesktop', {
      configurable: true,
      value: { version: '1.0.0' },
    });

    renderButton(23, '桌面端报告');

    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(mockedGetShareImage).not.toHaveBeenCalled();
  });

  it('renders share images through the desktop bridge and downloads them as PNG', async () => {
    const renderShareImage = vi.fn().mockResolvedValue(new TextEncoder().encode('png').buffer);
    Object.defineProperty(window, 'dsaDesktop', {
      configurable: true,
      value: { version: '3.30.0', renderShareImage },
    });
    const anchors: HTMLAnchorElement[] = [];
    vi.mocked(HTMLAnchorElement.prototype.click).mockImplementation(function (this: HTMLAnchorElement) {
      anchors.push(this);
    });
    renderButton(24, '桌面端报告');

    const dialog = await openDialogAndWaitForPreview();
    await waitFor(() => expect(renderShareImage).toHaveBeenCalledWith(24, 'card'));
    fireEvent.click(within(dialog).getByRole('button', { name: '下载图片' }));

    expect(mockedGetShareImage).not.toHaveBeenCalled();
    expect(anchors[0].download).toBe('桌面端报告-24_card.png');
  });
  it('tells the user to download instead when copying a large image fails', async () => {
    enableClipboard(vi.fn().mockRejectedValue(new Error('Canvas area exceeds the maximum limit')));
    mockedGetShareImage.mockResolvedValue(jpegBlob());
    renderButton();

    const dialog = await openDialogAndWaitForPreview();
    fireEvent.click(within(dialog).getByRole('button', { name: '复制图片' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('复制图片失败');
    expect(alert).toHaveTextContent('改用“下载图片”');
  });
});

describe('ShareImageButton layout switch', () => {
  beforeEach(() => {
    mockedGetShareImage.mockReset();
    let urlCounter = 0;
    vi.spyOn(URL, 'createObjectURL').mockImplementation(() => {
      urlCounter += 1;
      return `blob:share-image-${urlCounter}`;
    });
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    Object.defineProperty(window, 'dsaDesktop', { configurable: true, value: undefined });
    Object.defineProperty(window, 'ClipboardItem', { configurable: true, writable: true, value: undefined });
    setNavigatorValue('share', undefined);
    setNavigatorValue('canShare', undefined);
    setNavigatorValue('clipboard', undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const layoutButton = (dialog: HTMLElement, name: string) => within(
    within(dialog).getByRole('group', { name: '分享图版式' }),
  ).getByRole('button', { name });

  it('defaults to the card layout on the report page', async () => {
    mockedGetShareImage.mockResolvedValue(jpegBlob());
    renderButton(40, '报告页');

    const dialog = await openDialogAndWaitForPreview();

    expect(mockedGetShareImage).toHaveBeenCalledTimes(1);
    expect(mockedGetShareImage).toHaveBeenCalledWith(40, 'card');
    expect(layoutButton(dialog, '决策卡')).toHaveAttribute('aria-pressed', 'true');
    expect(layoutButton(dialog, '完整报告')).toHaveAttribute('aria-pressed', 'false');
  });

  it('defaults to the full layout when opened from the full report drawer', async () => {
    mockedGetShareImage.mockResolvedValue(jpegBlob());
    render(<ShareImageButton recordId={41} reportTitle="抽屉" reportLanguage="zh" defaultLayout="full" />);

    const dialog = await openDialogAndWaitForPreview();

    expect(mockedGetShareImage).toHaveBeenCalledTimes(1);
    expect(mockedGetShareImage).toHaveBeenCalledWith(41, 'full');
    expect(layoutButton(dialog, '完整报告')).toHaveAttribute('aria-pressed', 'true');
    expect(within(dialog).getByRole('img', { name: '分享图预览' })).toHaveAttribute('data-layout', 'full');
  });

  it('reloads with a loading state on switch and reuses generated layouts', async () => {
    const pending: Record<string, (blob: Blob) => void> = {};
    mockedGetShareImage.mockImplementation((_recordId, layout = 'card') => new Promise<Blob>((resolve) => {
      pending[layout] = resolve;
    }));
    const anchors: HTMLAnchorElement[] = [];
    vi.mocked(HTMLAnchorElement.prototype.click).mockImplementation(function (this: HTMLAnchorElement) {
      anchors.push(this);
    });
    render(<ShareImageButton recordId={42} reportTitle="贵州茅台-600519" reportLanguage="zh" defaultLayout="full" />);

    fireEvent.click(screen.getByRole('button', { name: '分享' }));
    const dialog = screen.getByRole('dialog', { name: '分享图' });
    await act(async () => {
      pending.full(jpegBlob());
    });
    const fullSrc = within(dialog).getByRole('img', { name: '分享图预览' }).getAttribute('src');

    fireEvent.click(layoutButton(dialog, '决策卡'));
    expect(mockedGetShareImage).toHaveBeenLastCalledWith(42, 'card');
    expect(within(dialog).getByRole('status')).toHaveTextContent('正在生成分享图...');
    expect(within(dialog).queryByRole('img', { name: '分享图预览' })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: '下载图片' })).not.toBeInTheDocument();

    await act(async () => {
      pending.card(pngBlob());
    });
    const cardImage = within(dialog).getByRole('img', { name: '分享图预览' });
    expect(cardImage).toHaveAttribute('data-layout', 'card');
    expect(cardImage.getAttribute('src')).not.toBe(fullSrc);
    fireEvent.click(within(dialog).getByRole('button', { name: '下载图片' }));
    expect(anchors[0].download).toBe('贵州茅台-600519-42_card.png');

    // 切回已生成的完整报告：不重复请求，直接显示缓存图片
    fireEvent.click(layoutButton(dialog, '完整报告'));
    expect(within(dialog).queryByRole('status')).not.toBeInTheDocument();
    expect(within(dialog).getByRole('img', { name: '分享图预览' })).toHaveAttribute('src', fullSrc);
    fireEvent.click(within(dialog).getByRole('button', { name: '下载图片' }));
    expect(anchors[1].download).toBe('贵州茅台-600519-42_full.jpg');

    fireEvent.click(layoutButton(dialog, '决策卡'));
    expect(mockedGetShareImage).toHaveBeenCalledTimes(2);
  });

  it('shares and copies the image of the selected layout', async () => {
    const nativeShare = vi.fn().mockResolvedValue(undefined);
    const write = vi.fn().mockResolvedValue(undefined);
    enableFileShare(nativeShare);
    enableClipboard(write);
    const cardImage = pngBlob();
    const fullImage = new Blob(['full-png'], { type: 'image/png' });
    mockedGetShareImage.mockImplementation(async (_recordId, layout = 'card') => (layout === 'full' ? fullImage : cardImage));
    renderButton(43, '报告页');

    const dialog = await openDialogAndWaitForPreview();
    fireEvent.click(layoutButton(dialog, '完整报告'));
    await within(dialog).findByRole('img', { name: '分享图预览' });

    fireEvent.click(within(dialog).getByRole('button', { name: '系统分享' }));
    expect(nativeShare.mock.calls[0][0].files[0].name).toBe('报告页-43_full.png');
    fireEvent.click(within(dialog).getByRole('button', { name: '复制图片' }));
    const [item] = write.mock.calls[0][0] as FakeClipboardItem[];
    await expect(item.items['image/png']).resolves.toBe(fullImage);
  });

  it('falls back to the generated layout when switching fails', async () => {
    mockedGetShareImage.mockImplementation(async (_recordId, layout = 'card') => {
      if (layout === 'full') throw new Error('full render failed');
      return jpegBlob();
    });
    renderButton(44, '报告页');

    const dialog = await openDialogAndWaitForPreview();
    fireEvent.click(layoutButton(dialog, '完整报告'));

    expect(await screen.findByRole('alert')).toHaveTextContent('生成分享图失败');
    expect(screen.getByRole('dialog', { name: '分享图' })).toBeInTheDocument();
    expect(layoutButton(dialog, '决策卡')).toHaveAttribute('aria-pressed', 'true');
    expect(within(dialog).getByRole('img', { name: '分享图预览' })).toHaveAttribute('data-layout', 'card');
  });

  it('passes the selected layout to the desktop bridge', async () => {
    const renderShareImage = vi.fn().mockResolvedValue(new TextEncoder().encode('png').buffer);
    Object.defineProperty(window, 'dsaDesktop', {
      configurable: true,
      value: { version: '3.30.0', renderShareImage },
    });
    render(<ShareImageButton recordId={45} reportTitle="桌面端报告" reportLanguage="zh" defaultLayout="full" />);

    const dialog = await openDialogAndWaitForPreview();
    expect(renderShareImage).toHaveBeenCalledWith(45, 'full');
    fireEvent.click(layoutButton(dialog, '决策卡'));
    await waitFor(() => expect(renderShareImage).toHaveBeenLastCalledWith(45, 'card'));
    expect(mockedGetShareImage).not.toHaveBeenCalled();
  });

  it('renders English layout labels', async () => {
    mockedGetShareImage.mockResolvedValue(jpegBlob());
    render(<ShareImageButton recordId={46} reportTitle="Report" reportLanguage="en" />);

    fireEvent.click(screen.getByRole('button', { name: 'Share' }));
    const group = within(screen.getByRole('dialog', { name: 'Share image' })).getByRole('group', { name: 'Share image layout' });
    expect(within(group).getByRole('button', { name: 'Card' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(group).getByRole('button', { name: 'Full report' })).toBeInTheDocument();
  });
});

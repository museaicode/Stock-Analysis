import { fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { historyApi } from '../../../api/history';
import { Drawer } from '../../common/Drawer';
import { OVERLAY_Z_INDEX } from '../../common/overlayLayers';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { ShareImageButton } from '../ShareImageButton';

vi.mock('../../../api/history', () => ({
  historyApi: {
    getShareImage: vi.fn(),
  },
}));

const mockedGetShareImage = vi.mocked(historyApi.getShareImage);

const onDrawerClose = vi.fn();

// Mirrors ReportMarkdownDrawer: the share button lives inside the drawer panel.
const DrawerWithShare = () => {
  const [open, setOpen] = useState(true);
  return (
    <UiLanguageProvider>
      <Drawer
        isOpen={open}
        onClose={() => {
          onDrawerClose();
          setOpen(false);
        }}
        zIndex={OVERLAY_Z_INDEX.reportDrawer}
      >
        <div data-testid="drawer-panel">
          <ShareImageButton recordId={31} reportTitle="贵州茅台-600519" reportLanguage="zh" />
        </div>
      </Drawer>
    </UiLanguageProvider>
  );
};

const openShareDialog = async () => {
  render(<DrawerWithShare />);
  fireEvent.click(screen.getByRole('button', { name: '分享' }));
  const dialog = screen.getByRole('dialog', { name: '分享图' });
  await within(dialog).findByRole('img', { name: '分享图预览' });
  return dialog;
};

describe('ShareImageButton inside a drawer', () => {
  beforeEach(() => {
    onDrawerClose.mockReset();
    mockedGetShareImage.mockReset();
    mockedGetShareImage.mockResolvedValue(new Blob(['png'], { type: 'image/png' }));
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:share-image');
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders the dialog under document.body above the drawer layer', async () => {
    const dialog = await openShareDialog();
    const overlay = screen.getByTestId('share-image-overlay');

    expect(overlay.parentElement).toBe(document.body);
    expect(screen.getByTestId('drawer-panel').contains(dialog)).toBe(false);
    expect(Number(overlay.style.zIndex)).toBeGreaterThan(OVERLAY_Z_INDEX.reportDrawer);
    expect(Number(overlay.style.zIndex)).toBeLessThan(OVERLAY_Z_INDEX.tooltip);
  });

  it('clicks inside the dialog close nothing; the overlay closes only the dialog', async () => {
    const dialog = await openShareDialog();

    fireEvent.click(within(dialog).getByRole('img', { name: '分享图预览' }));
    fireEvent.click(within(dialog).getByRole('button', { name: '下载图片' }));
    expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('dialog', { name: '分享图' })).toBeInTheDocument();
    expect(onDrawerClose).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('share-image-overlay'));
    expect(screen.queryByRole('dialog', { name: '分享图' })).not.toBeInTheDocument();
    expect(onDrawerClose).not.toHaveBeenCalled();
    expect(screen.getByTestId('drawer-panel')).toBeInTheDocument();
  });

  it('raises the share toast above the drawer layer', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    mockedGetShareImage.mockRejectedValueOnce(new Error('renderer unavailable'));
    render(<DrawerWithShare />);
    fireEvent.click(screen.getByRole('button', { name: '分享' }));

    const alert = await screen.findByRole('alert');
    const viewport = alert.parentElement as HTMLElement;
    expect(viewport.parentElement).toBe(document.body);
    expect(Number(viewport.style.zIndex)).toBeGreaterThan(OVERLAY_Z_INDEX.reportDrawer);
    expect(onDrawerClose).not.toHaveBeenCalled();
  });

  it('the close button closes only the dialog', async () => {
    const dialog = await openShareDialog();
    fireEvent.click(within(dialog).getByRole('button', { name: /^关闭$/ }));
    expect(screen.queryByRole('dialog', { name: '分享图' })).not.toBeInTheDocument();
    expect(onDrawerClose).not.toHaveBeenCalled();
  });

  it('Escape closes the top-most layer first, then the drawer', async () => {
    await openShareDialog();

    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: '分享图' })).not.toBeInTheDocument();
    expect(onDrawerClose).not.toHaveBeenCalled();
    expect(screen.getByTestId('drawer-panel')).toBeInTheDocument();

    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    expect(onDrawerClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('drawer-panel')).not.toBeInTheDocument();
  });

  it('moves focus into the dialog, traps Tab, and returns focus to the share button', async () => {
    const dialog = await openShareDialog();
    const closeButton = within(dialog).getByRole('button', { name: /^关闭$/ });
    const downloadButton = within(dialog).getByRole('button', { name: '下载图片' });
    expect(document.activeElement).toBe(closeButton);

    downloadButton.focus();
    fireEvent.keyDown(downloadButton, { key: 'Tab' });
    expect(document.activeElement).toBe(closeButton);
    fireEvent.keyDown(closeButton, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(downloadButton);

    fireEvent.keyDown(downloadButton, { key: 'Escape' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: '分享' }));
  });
});

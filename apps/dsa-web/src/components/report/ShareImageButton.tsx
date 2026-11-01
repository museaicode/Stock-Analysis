import type React from 'react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Copy, Download, Loader2, Share2, X } from 'lucide-react';
import { historyApi, type ShareImageLayout } from '../../api/history';
import { getParsedApiError, isParsedApiError } from '../../api/error';
import type { ReportLanguage } from '../../types/analysis';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import { InlineAlert } from '../common/InlineAlert';
import { OVERLAY_Z_INDEX } from '../common/overlayLayers';
import { ToastViewport } from '../common/ToastViewport';
import { Tooltip } from '../common/Tooltip';

type DesktopWindow = Window & {
  dsaDesktop?: {
    renderShareImage?: (recordId: number, layout?: ShareImageLayout) => Promise<ArrayBuffer>;
  };
};

type ShareToast = {
  variant: 'success' | 'danger';
  title: string;
  message?: string;
};

interface ShareImageButtonProps {
  recordId?: number;
  reportTitle: string;
  reportLanguage?: ReportLanguage;
  /** 打开预览弹窗时默认选中的版式：报告页为决策卡，"完整分析报告"抽屉为完整报告 */
  defaultLayout?: ShareImageLayout;
  className?: string;
}

type LoadedShareImage = { blob: Blob; previewUrl: string };

type ShareImageCache = {
  recordId: number;
  images: Partial<Record<ShareImageLayout, LoadedShareImage>>;
};

const SHARE_IMAGE_LAYOUTS: readonly ShareImageLayout[] = ['card', 'full'];

const TOAST_DURATION_MS = 4000;

const FOCUSABLE_SELECTOR = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const safeFilenamePart = (value: string): string => {
  const normalized = value.trim().replace(/[\\/:*?"<>|]+/g, '-').replace(/\s+/g, '-');
  return normalized.slice(0, 72) || 'report';
};

const extensionForImageType = (type: string): string => {
  if (type === 'image/jpeg') return 'jpg';
  if (type === 'image/webp') return 'webp';
  return 'png';
};

const downloadBlob = (blob: Blob, filename: string): void => {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
};

const errorName = (error: unknown): string | undefined => (
  typeof error === 'object' && error !== null && 'name' in error
    ? String((error as { name?: unknown }).name)
    : undefined
);

const errorMessage = (error: unknown): string | undefined => {
  if (isParsedApiError(error) || (typeof error === 'object' && error !== null && 'parsedError' in error)) {
    const parsed = getParsedApiError(error);
    // 有 HTTP 响应时优先展示服务端返回的具体原因（如转图工具不可用），网络层错误使用分类后的提示
    return parsed.status !== undefined ? parsed.rawMessage : parsed.message;
  }
  if (error instanceof Error && error.message) return error.message;
  return undefined;
};

const supportsImageClipboard = (): boolean => (
  typeof navigator !== 'undefined'
  && typeof navigator.clipboard?.write === 'function'
  && typeof window !== 'undefined'
  && typeof (window as Window & { ClipboardItem?: unknown }).ClipboardItem === 'function'
);

const supportsFileShare = (file: File | null): boolean => {
  if (!file || typeof navigator === 'undefined') return false;
  if (typeof navigator.share !== 'function' || typeof navigator.canShare !== 'function') return false;
  try {
    return navigator.canShare({ files: [file] });
  } catch {
    return false;
  }
};

/**
 * 剪贴板普遍只接受 image/png；JPEG 分享图在写入前转为 PNG。
 * 转换以 Promise 形式交给 ClipboardItem，使 clipboard.write 仍在点击事件内同步调用。
 */
const toPngBlob = (blob: Blob): Promise<Blob> => {
  if (blob.type === 'image/png') return Promise.resolve(blob);
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(blob);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(objectUrl);
      const canvas = document.createElement('canvas');
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const context = canvas.getContext('2d');
      if (!context) {
        reject(new Error('Canvas 2D context unavailable'));
        return;
      }
      context.drawImage(image, 0, 0);
      canvas.toBlob((pngBlob) => {
        if (pngBlob) resolve(pngBlob);
        else reject(new Error('PNG conversion failed'));
      }, 'image/png');
    };
    image.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error('Image decode failed'));
    };
    image.src = objectUrl;
  });
};

export const ShareImageButton: React.FC<ShareImageButtonProps> = ({
  recordId,
  reportTitle,
  reportLanguage = 'zh',
  defaultLayout = 'card',
  className = '',
}) => {
  const desktopRuntime = typeof window !== 'undefined' ? (window as DesktopWindow).dsaDesktop : undefined;
  const renderDesktopShareImage = desktopRuntime?.renderShareImage;
  const activeRecordId = desktopRuntime && !renderDesktopShareImage ? undefined : recordId;
  const text = getReportText(normalizeReportLanguage(reportLanguage));
  const titleId = useId();

  // 弹窗与图片都绑定记录 ID，切换记录后自动失效，无需在 effect 中重置状态；
  // 图片按版式分别缓存，弹窗内切换版式时已生成的版式不重复请求。
  const [openRecordId, setOpenRecordId] = useState<number | null>(null);
  const [selectedLayout, setSelectedLayout] = useState<ShareImageLayout>(defaultLayout);
  const [cache, setCache] = useState<ShareImageCache | null>(null);
  const [toast, setToast] = useState<ShareToast | null>(null);
  const generationRef = useRef(0);
  const inFlightRef = useRef<Set<string>>(new Set());
  const ownedUrlsRef = useRef<Set<string>>(new Set());
  const toastTimerRef = useRef<number | null>(null);
  const selectedLayoutRef = useRef<ShareImageLayout>(defaultLayout);
  const cacheRef = useRef<ShareImageCache | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

  const isOpen = activeRecordId !== undefined && openRecordId === activeRecordId;
  const recordImages = cache && cache.recordId === activeRecordId ? cache.images : null;
  const currentImage = recordImages?.[selectedLayout] ?? null;
  const blob = currentImage?.blob ?? null;
  const previewUrl = currentImage?.previewUrl ?? null;
  const isLoading = isOpen && !blob;

  const showToast = useCallback((nextToast: ShareToast) => {
    if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current);
    setToast(nextToast);
    toastTimerRef.current = window.setTimeout(() => {
      toastTimerRef.current = null;
      setToast(null);
    }, TOAST_DURATION_MS);
  }, []);

  const reportFailure = useCallback((title: string, error: unknown) => {
    console.error(`${title}:`, error);
    showToast({
      variant: 'danger',
      title,
      message: errorName(error) === 'NotAllowedError' ? text.shareImageNotAllowed : errorMessage(error),
    });
  }, [showToast, text.shareImageNotAllowed]);

  // 复制失败多因长图超出浏览器画布尺寸限制（如 iOS Safari），提示改用下载
  const reportCopyFailure = useCallback((error: unknown) => {
    console.error(`${text.shareImageCopyFailed}:`, error);
    showToast({
      variant: 'danger',
      title: text.shareImageCopyFailed,
      message: errorName(error) === 'NotAllowedError' ? text.shareImageNotAllowed : text.shareImageCopyFailedUseDownload,
    });
  }, [showToast, text.shareImageCopyFailed, text.shareImageCopyFailedUseDownload, text.shareImageNotAllowed]);

  // 切换记录或卸载时丢弃进行中的请求
  useEffect(() => () => {
    generationRef.current += 1;
    inFlightRef.current.clear();
  }, [activeRecordId]);

  useEffect(() => () => {
    if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current);
  }, []);

  useEffect(() => {
    selectedLayoutRef.current = selectedLayout;
    cacheRef.current = cache;
  }, [cache, selectedLayout]);

  // 缓存被替换（切换记录）时释放不再引用的预览 URL；卸载时全部释放
  useEffect(() => {
    const live = new Set(Object.values(cache?.images ?? {}).map((item) => item.previewUrl));
    ownedUrlsRef.current.forEach((url) => {
      if (!live.has(url)) {
        URL.revokeObjectURL(url);
        ownedUrlsRef.current.delete(url);
      }
    });
  }, [cache]);

  useEffect(() => {
    const ownedUrls = ownedUrlsRef.current;
    return () => {
      ownedUrls.forEach((url) => URL.revokeObjectURL(url));
      ownedUrls.clear();
    };
  }, []);

  // 关闭弹窗不取消进行中的生成，完成后缓存，重新打开时直接展示
  const closeDialog = useCallback(() => {
    setOpenRecordId(null);
  }, []);

  // 弹窗可能从抽屉内打开：在捕获阶段处理 Esc 并 preventDefault，抽屉据此跳过本次 Esc，
  // 保证只关闭最上层；Tab 焦点限制在弹窗内，关闭后焦点回到触发按钮。
  useEffect(() => {
    if (!isOpen) return undefined;
    const trigger = triggerRef.current;
    closeButtonRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (event.defaultPrevented) return;
        event.preventDefault();
        closeDialog();
        return;
      }
      if (event.key !== 'Tab') return;
      const dialogNode = dialogRef.current;
      if (!dialogNode) return;
      const focusable = Array.from(dialogNode.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (!dialogNode.contains(active)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', handleKeyDown, true);
    return () => {
      document.removeEventListener('keydown', handleKeyDown, true);
      if (trigger?.isConnected) trigger.focus();
    };
  }, [closeDialog, isOpen]);

  const loadLayout = useCallback((targetRecordId: number, layout: ShareImageLayout) => {
    if (recordImages?.[layout]) return;
    const key = `${targetRecordId}:${layout}`;
    if (inFlightRef.current.has(key)) return;

    const generation = generationRef.current;
    inFlightRef.current.add(key);
    void (async () => {
      try {
        const loaded = renderDesktopShareImage
          ? new Blob([await renderDesktopShareImage(targetRecordId, layout)], { type: 'image/png' })
          : await historyApi.getShareImage(targetRecordId, layout);
        if (generationRef.current !== generation) return;
        const previewUrl = URL.createObjectURL(loaded);
        ownedUrlsRef.current.add(previewUrl);
        setCache((previous) => {
          const images = previous && previous.recordId === targetRecordId ? previous.images : {};
          return { recordId: targetRecordId, images: { ...images, [layout]: { blob: loaded, previewUrl } } };
        });
      } catch (error) {
        if (generationRef.current !== generation) return;
        reportFailure(text.shareImageLoadFailed, error);
        // 当前选中版式生成失败：若另一版式已生成则切回它，否则关闭弹窗，可再次点击"分享"重试
        if (selectedLayoutRef.current !== layout) return;
        const latest = cacheRef.current;
        const images = latest && latest.recordId === targetRecordId ? latest.images : {};
        const fallback = SHARE_IMAGE_LAYOUTS.find((candidate) => candidate !== layout && images[candidate]);
        if (fallback) setSelectedLayout(fallback);
        else setOpenRecordId(null);
      } finally {
        if (generationRef.current === generation) inFlightRef.current.delete(key);
      }
    })();
  }, [recordImages, renderDesktopShareImage, reportFailure, text.shareImageLoadFailed]);

  // 第一步：点击后立即打开弹窗（加载态），默认选中当前入口对应的版式，图片在弹窗内异步生成。
  // 复制 / 系统分享由弹窗内的下一次点击触发，届时拥有新的用户激活。
  const handleOpen = useCallback(() => {
    if (activeRecordId === undefined) return;
    setOpenRecordId(activeRecordId);
    setSelectedLayout(defaultLayout);
    loadLayout(activeRecordId, defaultLayout);
  }, [activeRecordId, defaultLayout, loadLayout]);

  const handleSelectLayout = useCallback((layout: ShareImageLayout) => {
    if (activeRecordId === undefined || layout === selectedLayout) return;
    setSelectedLayout(layout);
    loadLayout(activeRecordId, layout);
  }, [activeRecordId, loadLayout, selectedLayout]);

  const imageType = blob?.type || 'image/png';
  const filename = activeRecordId === undefined
    ? ''
    : `${safeFilenamePart(reportTitle)}-${activeRecordId}_${selectedLayout}.${extensionForImageType(imageType)}`;
  const file = useMemo(
    () => (blob && filename ? new File([blob], filename, { type: imageType }) : null),
    [blob, filename, imageType],
  );
  const canCopy = Boolean(blob) && supportsImageClipboard();
  const canShare = supportsFileShare(file);

  const handleDownload = useCallback(() => {
    if (!blob) return;
    try {
      downloadBlob(blob, filename);
    } catch (error) {
      reportFailure(text.shareImageDownloadFailed, error);
    }
  }, [blob, filename, reportFailure, text.shareImageDownloadFailed]);

  const handleCopy = useCallback(() => {
    if (!blob) return;
    try {
      // 必须在点击事件内同步调用 clipboard.write，才能使用本次点击的用户激活
      const item = new ClipboardItem({ 'image/png': toPngBlob(blob) });
      navigator.clipboard.write([item]).then(
        () => showToast({ variant: 'success', title: text.shareImageCopied }),
        (error: unknown) => reportCopyFailure(error),
      );
    } catch (error) {
      reportCopyFailure(error);
    }
  }, [blob, reportCopyFailure, showToast, text.shareImageCopied]);

  const handleSystemShare = useCallback(() => {
    if (!file) return;
    try {
      // 同步调用 navigator.share，使用弹窗内本次点击的用户激活
      navigator.share({ files: [file], title: reportTitle }).catch((error: unknown) => {
        if (errorName(error) === 'AbortError') return; // 用户主动取消，不提示
        reportFailure(text.shareImageShareFailed, error);
      });
    } catch (error) {
      reportFailure(text.shareImageShareFailed, error);
    }
  }, [file, reportFailure, reportTitle, text.shareImageShareFailed]);

  if (activeRecordId === undefined) return null;

  const actionButtonClass = 'inline-flex h-9 items-center justify-center gap-1.5 rounded-lg px-3 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60';

  const dialog = isOpen ? (
    <div
      className="fixed inset-0 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      style={{ zIndex: OVERLAY_Z_INDEX.modal }}
      data-testid="share-image-overlay"
      onClick={(event) => {
        // Portal 内的点击在 React 树中仍会冒泡到抽屉等祖先组件，这里统一截断；
        // 只有点在遮罩本身时才关闭弹窗。
        event.stopPropagation();
        if (event.target === event.currentTarget) closeDialog();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="flex max-h-[calc(100vh-32px)] w-full max-w-lg flex-col overflow-hidden rounded-xl border border-border/70 bg-elevated shadow-2xl"
      >
        <div className="flex items-center justify-between gap-3 border-b border-border/60 px-4 py-3">
          <h3 id={titleId} className="min-w-0 truncate text-base font-medium text-foreground">
            {text.shareImageDialogTitle}
          </h3>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={closeDialog}
            className="rounded-md p-1 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
            aria-label={text.closeShareImageDialog}
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>

        <div className="border-b border-border/60 px-4 py-2">
          <div
            role="group"
            aria-label={text.shareImageLayoutLabel}
            className="inline-flex rounded-lg border border-border/70 bg-base/40 p-0.5"
            data-testid="share-image-layout-switch"
          >
            {SHARE_IMAGE_LAYOUTS.map((layout) => {
              const selected = layout === selectedLayout;
              return (
                <button
                  key={layout}
                  type="button"
                  aria-pressed={selected}
                  data-layout={layout}
                  onClick={() => handleSelectLayout(layout)}
                  className={`h-8 rounded-md px-3 text-sm font-medium transition-colors ${
                    selected
                      ? 'bg-cyan/80 text-foreground shadow-sm'
                      : 'text-secondary-text hover:bg-hover hover:text-foreground'
                  }`}
                >
                  {layout === 'full' ? text.shareImageLayoutFull : text.shareImageLayoutCard}
                </button>
              );
            })}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto bg-base/40 p-4">
          {isLoading || !previewUrl ? (
            <div role="status" className="flex h-48 flex-col items-center justify-center gap-3 text-sm text-secondary-text">
              <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
              <span>{text.generatingShareImage}</span>
            </div>
          ) : (
            <img
              src={previewUrl}
              data-layout={selectedLayout}
              alt={text.shareImagePreviewAlt}
              className="mx-auto block w-full rounded-lg border border-border/50"
            />
          )}
        </div>

        {blob ? (
          <div className="flex flex-wrap justify-end gap-2 border-t border-border/60 px-4 py-3">
            <button
              type="button"
              onClick={handleDownload}
              className={`${actionButtonClass} border border-border/70 text-secondary-text hover:bg-hover hover:text-foreground`}
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              {text.downloadShareImage}
            </button>
            {canCopy ? (
              <button
                type="button"
                onClick={handleCopy}
                className={`${actionButtonClass} border border-border/70 text-secondary-text hover:bg-hover hover:text-foreground`}
              >
                <Copy className="h-4 w-4" aria-hidden="true" />
                {text.copyShareImage}
              </button>
            ) : null}
            {canShare ? (
              <button
                type="button"
                onClick={handleSystemShare}
                className={`${actionButtonClass} bg-cyan/80 text-foreground shadow-lg shadow-cyan/20 hover:bg-cyan`}
              >
                <Share2 className="h-4 w-4" aria-hidden="true" />
                {text.systemShareImage}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  ) : null;

  const toastNode = toast ? (
    <ToastViewport style={{ zIndex: OVERLAY_Z_INDEX.toast }}>
      <InlineAlert
        variant={toast.variant}
        title={toast.title}
        message={toast.message ?? ''}
        className="pointer-events-auto bg-card/95 backdrop-blur-sm"
      />
    </ToastViewport>
  ) : null;

  return (
    <>
      <Tooltip content={text.generateShareImage}>
        <span className="inline-flex shrink-0">
          <button
            ref={triggerRef}
            type="button"
            onClick={handleOpen}
            className={`home-surface-button flex h-10 shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg px-3 text-sm font-medium text-secondary-text hover:text-foreground disabled:opacity-50 ${className}`}
            aria-label={text.generateShareImage}
            aria-haspopup="dialog"
          >
            <Share2 className="h-5 w-5" aria-hidden="true" />
            <span>{text.generateShareImage}</span>
          </button>
        </span>
      </Tooltip>
      {typeof document !== 'undefined' && dialog ? createPortal(dialog, document.body) : null}
      {typeof document !== 'undefined' && toastNode ? createPortal(toastNode, document.body) : null}
    </>
  );
};

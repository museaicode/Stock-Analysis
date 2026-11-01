import type React from 'react';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Copy, Download, Loader2, Share2, X } from 'lucide-react';
import { historyApi } from '../../api/history';
import { getParsedApiError, isParsedApiError } from '../../api/error';
import type { ReportLanguage } from '../../types/analysis';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import { InlineAlert } from '../common/InlineAlert';
import { OVERLAY_Z_INDEX } from '../common/overlayLayers';
import { ToastViewport } from '../common/ToastViewport';
import { Tooltip } from '../common/Tooltip';

type DesktopWindow = Window & {
  dsaDesktop?: {
    renderShareImage?: (recordId: number) => Promise<ArrayBuffer>;
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
  className?: string;
}

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
  className = '',
}) => {
  const desktopRuntime = typeof window !== 'undefined' ? (window as DesktopWindow).dsaDesktop : undefined;
  const renderDesktopShareImage = desktopRuntime?.renderShareImage;
  const activeRecordId = desktopRuntime && !renderDesktopShareImage ? undefined : recordId;
  const text = getReportText(normalizeReportLanguage(reportLanguage));
  const titleId = useId();

  // 弹窗与图片都绑定记录 ID，切换记录后自动失效，无需在 effect 中重置状态
  const [openRecordId, setOpenRecordId] = useState<number | null>(null);
  const [image, setImage] = useState<{ recordId: number; blob: Blob; previewUrl: string } | null>(null);
  const [toast, setToast] = useState<ShareToast | null>(null);
  const loadTokenRef = useRef(0);
  const inFlightRecordRef = useRef<number | null>(null);
  const toastTimerRef = useRef<number | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

  const isOpen = activeRecordId !== undefined && openRecordId === activeRecordId;
  const currentImage = image && image.recordId === activeRecordId ? image : null;
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

  // 切换记录或卸载时丢弃进行中的请求
  useEffect(() => () => {
    loadTokenRef.current += 1;
    inFlightRecordRef.current = null;
  }, [activeRecordId]);

  useEffect(() => () => {
    if (toastTimerRef.current !== null) window.clearTimeout(toastTimerRef.current);
  }, []);

  // 图片被替换或组件卸载时释放预览 URL
  useEffect(() => {
    if (!image) return undefined;
    return () => URL.revokeObjectURL(image.previewUrl);
  }, [image]);

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

  // 第一步：点击后立即打开弹窗（加载态），图片在弹窗内异步生成。
  // 复制 / 系统分享由弹窗内的下一次点击触发，届时拥有新的用户激活。
  const handleOpen = useCallback(() => {
    if (activeRecordId === undefined) return;
    setOpenRecordId(activeRecordId);
    if (currentImage || inFlightRecordRef.current === activeRecordId) return;

    const loadToken = loadTokenRef.current + 1;
    loadTokenRef.current = loadToken;
    const targetRecordId = activeRecordId;
    inFlightRecordRef.current = targetRecordId;
    void (async () => {
      try {
        const loaded = renderDesktopShareImage
          ? new Blob([await renderDesktopShareImage(targetRecordId)], { type: 'image/png' })
          : await historyApi.getShareImage(targetRecordId);
        if (loadTokenRef.current !== loadToken) return;
        setImage({ recordId: targetRecordId, blob: loaded, previewUrl: URL.createObjectURL(loaded) });
      } catch (error) {
        if (loadTokenRef.current !== loadToken) return;
        setOpenRecordId(null);
        reportFailure(text.shareImageLoadFailed, error);
      } finally {
        if (loadTokenRef.current === loadToken) inFlightRecordRef.current = null;
      }
    })();
  }, [activeRecordId, currentImage, renderDesktopShareImage, reportFailure, text.shareImageLoadFailed]);

  const imageType = blob?.type || 'image/png';
  const filename = activeRecordId === undefined
    ? ''
    : `${safeFilenamePart(reportTitle)}-${activeRecordId}.${extensionForImageType(imageType)}`;
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
        (error: unknown) => reportFailure(text.shareImageCopyFailed, error),
      );
    } catch (error) {
      reportFailure(text.shareImageCopyFailed, error);
    }
  }, [blob, reportFailure, showToast, text.shareImageCopied, text.shareImageCopyFailed]);

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

        <div className="min-h-0 flex-1 overflow-y-auto bg-base/40 p-4">
          {isLoading || !previewUrl ? (
            <div role="status" className="flex h-48 flex-col items-center justify-center gap-3 text-sm text-secondary-text">
              <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
              <span>{text.generatingShareImage}</span>
            </div>
          ) : (
            <img
              src={previewUrl}
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

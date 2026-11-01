import type React from 'react';
import { useCallback, useEffect, useRef } from 'react';
import { Printer } from 'lucide-react';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { printReport, REPORT_PRINT_ROOT_ATTR, restorePendingReportPrint } from '../../utils/reportPrint';
import { Tooltip } from '../common/Tooltip';

interface PrintReportButtonProps {
  /** Suggested PDF file name (set as document.title while printing). */
  printTitle: string;
  disabled?: boolean;
}

/**
 * "Export PDF" button for the full analysis report. Uses the browser print
 * dialog (`window.print()` + `@media print` styles); the user picks
 * "Save as PDF" as the destination.
 */
export const PrintReportButton: React.FC<PrintReportButtonProps> = ({ printTitle, disabled = false }) => {
  const { t } = useUiLanguage();
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const label = t('report.exportPdf');

  useEffect(() => restorePendingReportPrint, []);

  const handleClick = useCallback(() => {
    const root = buttonRef.current?.closest(`[${REPORT_PRINT_ROOT_ATTR}]`) ?? null;
    printReport(printTitle, root);
  }, [printTitle]);

  return (
    <Tooltip content={t('report.exportPdfHint')}>
      <span className="inline-flex">
        <button
          ref={buttonRef}
          type="button"
          onClick={handleClick}
          disabled={disabled}
          className="home-surface-button flex h-10 items-center justify-center gap-1.5 rounded-lg px-3 text-sm text-secondary-text hover:text-foreground disabled:opacity-50"
          aria-label={label}
          data-testid="report-export-pdf"
        >
          <Printer className="h-5 w-5" aria-hidden="true" />
          <span className="hidden sm:inline">{label}</span>
        </button>
      </span>
    </Tooltip>
  );
};

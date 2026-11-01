/**
 * Stacking order for page-level overlays that render into `document.body`.
 *
 * The values mirror the layers already in use; anything opened from inside a
 * drawer (modal dialogs, their toasts) must sit above the highest drawer, while
 * tooltips stay on top of everything.
 */
export const OVERLAY_Z_INDEX = {
  /** Default `Drawer` layer. */
  drawer: 50,
  /** "Full analysis report" drawer (opened above the home / history drawers). */
  reportDrawer: 100,
  /** Modal dialogs, including ones opened from inside a drawer. */
  modal: 110,
  /** Toasts raised by modals or drawers. */
  toast: 115,
  /** Tooltips (`Tooltip` uses the matching `z-[120]`). */
  tooltip: 120,
} as const;

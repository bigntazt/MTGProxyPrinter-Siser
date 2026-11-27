# Documentation Index

- [Overview](/wiki/MTGProxyPrinter)
- [Changelog](/doc/$CURRENT/doc/changelog.md)
- [Installation instructions](/wiki/Installation)
- [Wiki](/wiki)

## Image Preparation Preview

Open **Application → Image Preparation Preview** to inspect the current page.
Choose **Original** for native source pixels, or **Lanczos resize** at **600 PPI**
(initial target), **900 PPI** or **1200 PPI**. The target applies only to Lanczos; resizing does
not recover missing detail. **Refresh** rereads sources. Use the existing task
**Cancel** button to stop preparation; it does not retry automatically.

Select **Off**, close or hide the dock to restore standard preview. Reopening and
each new application launch start Off; choices are not saved. This prepares
ordinary faces only; custom cards and composed CheckCards keep their usual preview.
Read the details for Original fallback, uncached results or memory-limit exclusions.

After Ready, choose **File → Export prepared current page as PDF…**. Review the
captured page and actual outcomes, then choose **Save PDF…**. Refresh preparation
first to include externally changed source images. Cancel closes without saving.
The saved PDF remains the captured revision even if the document changes later.

**Prepared images are used here and by this explicit current-page PDF command.
The prepared PNG submenu independently selects the page canvas at 600, 900 or 1200
PPI, including in Original mode. Regular Print, print preview, PDF and PNG use the
existing images.**
See [preview behavior and limits](architecture/prepared-image-preview.md).

## Prepared current-page PNG

After Image Preparation Preview is Ready, choose **File → Export prepared current
page as PNG → 600 / 900 / 1200 PPI**. Review the source preparation outcomes, page
pixel dimensions and white background, then Save PNG. This page resolution does
not change the prepared source pixels or restore missing detail. Large pages pause
the UI during rendering/encoding; an over-budget canvas can use lower PNG PPI or
prepared PDF. Cancel closes without saving. See [PNG limits and behavior](architecture/prepared-page-png.md).

## Optional neural worker (standalone)

M33 adds a separately invoked experimental RGB8 worker, with no application UI or
normal startup dependency. Original/Lanczos remain the baseline. See the
[worker protocol and inactive integration boundary](architecture/neural-worker.md).

The [explicit neural worker client](architecture/neural-worker-client.md) adds
process ownership and verified native RGB results for a future adapter. It remains
inactive in the application; decoding, alpha/color, target resizing and caching
are separate M35 work.

M35 adds an [inactive neural preparation/cache service](architecture/neural-image-preparation.md).
It preserves Original/Lanczos defaults and adds no application, preview or export
caller; subsequent adoption remains separately scoped.

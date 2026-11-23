# Documentation Index

- [Overview](/wiki/MTGProxyPrinter)
- [Changelog](/doc/$CURRENT/doc/changelog.md)
- [Installation instructions](/wiki/Installation)
- [Wiki](/wiki)

## Image Preparation Preview

Open **Application → Image Preparation Preview** to inspect the current page.
Choose **Original** for native source pixels, or **Lanczos resize** at **600 PPI**
(initial target) or **1200 PPI**. The target applies only to Lanczos; resizing does
not recover missing detail. **Refresh** rereads sources. Use the existing task
**Cancel** button to stop preparation; it does not retry automatically.

Select **Off**, close or hide the dock to restore standard preview. Reopening and
each new application launch start Off; choices are not saved. This prepares
ordinary faces only; custom cards and composed CheckCards keep their usual preview.
Read the details for Original fallback, uncached results or memory-limit exclusions.

**Preview only. Printing, PDF and PNG exports use the existing images.**
See [preview behavior and limits](architecture/prepared-image-preview.md).

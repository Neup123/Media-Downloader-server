"""Isolated pinned gallery-dl CLI adapter for actual URL identity selection.

The original CLI, extractor and DownloadJob implement network/download behavior.
This subclass limits the actual Message.Url handler to selected identities,
rejects child extractors, and bounds scanning to the preview's index window.
Never interpret an identity as a URL, command, or Python expression.
"""
from __future__ import annotations

import sys
from gallery_dl import job, exception, main as gallery_main

if __package__:
    from .gallery_selection import gallery_identities, gallery_url_identity, MAX_GALLERY_INDEX
else:
    from gallery_selection import gallery_identities, gallery_url_identity, MAX_GALLERY_INDEX


def selected_job_type(identities):
    selected = frozenset(gallery_identities(identities))

    class SelectedDownloadJob(job.DownloadJob):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.remaining = set(selected)
            self.scanned = 0

        def handle_url(self, url, kwdict):
            self.scanned += 1
            if self.scanned > MAX_GALLERY_INDEX:
                raise exception.StopExtraction()
            identity = gallery_url_identity(url)
            if identity not in self.remaining:
                return
            super().handle_url(url, kwdict)
            self.remaining.remove(identity)
            if not self.remaining:
                raise exception.StopExtraction()

        def handle_queue(self, url, kwdict):
            # Never follow child/gallery URLs in this flat-gallery contract.
            self.status |= 4
            raise exception.StopExtraction()

        def handle_finalize(self):
            super().handle_finalize()
            if self.remaining:
                self.status |= 4

    return SelectedDownloadJob


def main():
    if len(sys.argv) < 3:
        return 2
    try:
        selected = selected_job_type(sys.argv[1].split(","))
    except ValueError:
        return 2
    job.DownloadJob = selected
    sys.argv = ["gallery-dl", *sys.argv[2:]]
    return gallery_main()


if __name__ == "__main__":
    sys.exit(main())

"""Open the About page (docs/index.html) in the default web browser.

The page is a single self-contained HTML file (styles inlined, no CDN,
no external assets) so it works on offline workstations. Opened via
os.startfile, which resolves the OS file association — i.e. the default
browser. hou is only needed for status-bar feedback, so the tool also
runs (headless-testable) outside Houdini.
"""

import os

from houtools.core.constants import PROJECT_ROOT
from houtools.core.log import get_logger
from houtools.dev import status

log = get_logger("tools.about_houtools")

ABOUT_PAGE = PROJECT_ROOT / "docs" / "index.html"


def _status(message, error=False):
    """通知反馈（委托 dev.status → toast 通知）；无头自动降级。"""
    status(message, error=error)


def run(kwargs=None):
    """主菜单入口:用默认浏览器打开离线 About 页面。"""
    if not ABOUT_PAGE.is_file():
        message = "HouTools: About 页面缺失 {}".format(ABOUT_PAGE)
        log.warning("%s", message)
        _status(message, error=True)
        return
    os.startfile(str(ABOUT_PAGE))
    _status("HouTools: 已在默认浏览器打开 About 页面")

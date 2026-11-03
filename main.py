import sys
import traceback
from pathlib import Path

LOG = Path(__file__).resolve().parent / "data" / "error.log"


def _log_exc(exc_type, exc, tb):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        traceback.print_exception(exc_type, exc, tb, file=f)
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _log_exc

import threading  # noqa: E402

threading.excepthook = lambda a: _log_exc(a.exc_type, a.exc_value, a.exc_traceback)

from monitor.gui import App  # noqa: E402

if __name__ == "__main__":
    app = App()
    app.report_callback_exception = _log_exc
    app.mainloop()

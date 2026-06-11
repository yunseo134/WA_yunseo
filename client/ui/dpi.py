import os


def configure_high_dpi():
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")
    _set_windows_dpi_awareness()

    try:
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QApplication

        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        if hasattr(Qt, "AA_Use96Dpi"):
            QApplication.setAttribute(Qt.AA_Use96Dpi, False)
        rounding_policy = getattr(getattr(Qt, "HighDpiScaleFactorRoundingPolicy", None), "PassThrough", None)
        if rounding_policy is not None and hasattr(QApplication, "setHighDpiScaleFactorRoundingPolicy"):
            QApplication.setHighDpiScaleFactorRoundingPolicy(rounding_policy)
    except Exception:
        pass


def _set_windows_dpi_awareness():
    if os.name != "nt":
        return
    try:
        import ctypes

        awareness_context_per_monitor_v2 = ctypes.c_void_p(-4)
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(awareness_context_per_monitor_v2):
            return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

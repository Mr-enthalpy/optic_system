from __future__ import annotations


def test_fit_size_shrinks_wide_image():
    from scripts.monitor_run_status import _fit_size

    assert _fit_size(2000, 500, 500, 500) == (500, 125)


def test_fit_size_shrinks_tall_image():
    from scripts.monitor_run_status import _fit_size

    assert _fit_size(500, 2000, 500, 500) == (125, 500)


def test_fit_size_does_not_upscale_by_default():
    from scripts.monitor_run_status import _fit_size

    assert _fit_size(100, 50, 500, 500) == (100, 50)
    assert _fit_size(80, 120, 512, 512) == (80, 120)


def test_fit_size_handles_zero_panel_size():
    from scripts.monitor_run_status import _fit_size

    w, h = _fit_size(100, 50, 0, 0)
    assert w >= 1 and h >= 1


def test_fit_size_at_least_1x1():
    from scripts.monitor_run_status import _fit_size

    w, h = _fit_size(10000, 10000, 1, 1)
    assert w >= 1 and h >= 1


class FakeText:
    def __init__(self, yview=(0.3, 0.6)):
        self._yview = yview
        self.text = ""
        self.see_called = False
        self.moved_to = None

    def yview(self):
        return self._yview

    def yview_moveto(self, value):
        self.moved_to = float(value)

    def configure(self, **kwargs):
        pass

    def delete(self, *args):
        self.text = ""

    def insert(self, *args):
        self.text = args[-1]

    def see(self, index):
        self.see_called = True


def test_metadata_update_preserves_scroll():
    from scripts.monitor_run_status import _update_text_widget

    widget = FakeText(yview=(0.25, 0.50))
    _update_text_widget(widget, "new", preserve_scroll=True)
    assert widget.moved_to == 0.25
    assert not widget.see_called


def test_logs_follow_bottom_only_when_already_at_bottom():
    from scripts.monitor_run_status import _update_text_widget

    widget = FakeText(yview=(0.75, 0.99))
    _update_text_widget(
        widget,
        "new logs",
        preserve_scroll=True,
        follow_bottom_if_already_at_bottom=True,
    )
    assert widget.see_called


def test_logs_do_not_force_bottom_when_user_scrolled_up():
    from scripts.monitor_run_status import _update_text_widget

    widget = FakeText(yview=(0.10, 0.40))
    _update_text_widget(
        widget,
        "new logs",
        preserve_scroll=True,
        follow_bottom_if_already_at_bottom=True,
    )
    assert not widget.see_called
    assert widget.moved_to == 0.10


def test_text_widget_skips_update_when_text_unchanged():
    from scripts.monitor_run_status import _update_text_widget

    widget = FakeText()
    _update_text_widget(widget, "same", preserve_scroll=True)
    widget.moved_to = None
    _update_text_widget(widget, "same", preserve_scroll=True)
    # text unchanged, scroll not touched, widget not modified
    assert widget.moved_to is None

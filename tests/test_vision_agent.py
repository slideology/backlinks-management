import unittest
import tempfile
from unittest.mock import Mock

from vision_agent import (
    _build_focus_clip,
    _build_screenshot_options,
    _get_circuit_breaker_pause_reason,
    _capture_focused_comment_region,
    _extract_json,
    _format_failure_message,
    _is_retryable_vision_error,
    _record_vision_failure,
    _record_vision_success,
)


class _FakeLocatorNode:
    def __init__(self, visible=True, screenshot_bytes=b"locator-shot", box=None):
        self._visible = visible
        self._screenshot_bytes = screenshot_bytes
        self._box = box

    def is_visible(self):
        return self._visible

    def scroll_into_view_if_needed(self, timeout=None):
        return None

    def screenshot(self, timeout=None, **kwargs):
        return self._screenshot_bytes

    def bounding_box(self):
        return self._box


class _FakeLocator:
    def __init__(self, node=None):
        self.first = node or _FakeLocatorNode(visible=False)

    def count(self):
        return 1 if self.first.is_visible() else 0


class _FakePage:
    def __init__(self, selectors=None):
        self._selectors = selectors or {}
        self.screenshot_calls = []

    def locator(self, selector):
        return self._selectors.get(selector, _FakeLocator())

    def screenshot(self, **kwargs):
        self.screenshot_calls.append(kwargs)
        return b"page-shot"


class VisionAgentTests(unittest.TestCase):
    def test_extract_json_from_code_block(self):
        raw = """```json
        {"textarea_x": 100, "textarea_y": 200}
        ```"""
        parsed, error = _extract_json(raw)
        self.assertIsNone(error)
        self.assertEqual(parsed["textarea_x"], 100)

    def test_extract_json_invalid_payload(self):
        parsed, error = _extract_json("not-json")
        self.assertIsNone(parsed)
        self.assertEqual(error, "vision_invalid_json")

    def test_format_failure_message(self):
        self.assertIn("提交按钮", _format_failure_message("submit_not_found"))

    def test_build_focus_clip_clamps_to_visible_viewport(self):
        clip = _build_focus_clip(
            {"x": 100, "y": 500, "width": 200, "height": 80},
            {
                "viewport": {"width": 1280, "height": 800},
                "scroll": {"x": 0, "y": 400, "innerHeight": 647},
            },
        )
        self.assertEqual(clip["x"], 0)
        self.assertEqual(clip["y"], 400)
        self.assertEqual(clip["width"], 520)
        self.assertEqual(clip["height"], 360)

    def test_capture_focused_comment_region_prefers_comment_iframe(self):
        page = _FakePage(
            selectors={
                'iframe[src*="comment"]': _FakeLocator(
                    _FakeLocatorNode(
                        visible=True,
                        screenshot_bytes=b"iframe-shot",
                        box={"x": 40, "y": 120, "width": 600, "height": 320},
                    )
                ),
            }
        )

        screenshot_bytes, mime_type, image_ext, meta = _capture_focused_comment_region(
            page,
            {"viewport": {"width": 1280, "height": 800}, "scroll": {"x": 0, "y": 0, "innerHeight": 800}},
            {"image_type": "jpeg", "image_quality": 65, "max_image_side": 1400},
        )

        self.assertIsInstance(screenshot_bytes, bytes)
        self.assertEqual(mime_type, "image/jpeg")
        self.assertEqual(image_ext, "jpg")
        self.assertEqual(meta["capture_mode"], "clip")
        self.assertEqual(meta["focus_selector"], 'iframe[src*="comment"]')
        self.assertEqual(page.screenshot_calls[0]["type"], "jpeg")

    def test_capture_focused_comment_region_uses_expanded_clip_for_textarea(self):
        page = _FakePage(
            selectors={
                "textarea": _FakeLocator(
                    _FakeLocatorNode(
                        visible=True,
                        box={"x": 100, "y": 500, "width": 200, "height": 80},
                    )
                ),
            }
        )

        screenshot_bytes, mime_type, image_ext, meta = _capture_focused_comment_region(
            page,
            {"viewport": {"width": 1280, "height": 800}, "scroll": {"x": 0, "y": 400, "innerHeight": 647}},
            {"image_type": "jpeg", "image_quality": 65, "max_image_side": 1400},
        )

        self.assertEqual(screenshot_bytes, b"page-shot")
        self.assertEqual(mime_type, "image/jpeg")
        self.assertEqual(image_ext, "jpg")
        self.assertEqual(meta["capture_mode"], "clip")
        self.assertEqual(meta["focus_selector"], "textarea")
        self.assertEqual(page.screenshot_calls[0]["clip"], {"x": 0, "y": 400, "width": 520, "height": 360})

    def test_build_screenshot_options_for_jpeg(self):
        options, mime_type, image_ext = _build_screenshot_options({"image_type": "jpeg", "image_quality": 72})
        self.assertEqual(options["type"], "jpeg")
        self.assertEqual(options["quality"], 72)
        self.assertEqual(mime_type, "image/jpeg")
        self.assertEqual(image_ext, "jpg")

    def test_is_retryable_vision_error(self):
        self.assertTrue(_is_retryable_vision_error(RuntimeError("timed out")))
        self.assertFalse(_is_retryable_vision_error(RuntimeError("invalid api key")))

    def test_circuit_breaker_opens_after_repeated_timeouts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "circuit_breaker_file": f"{tmpdir}/breaker.json",
                "circuit_breaker_failures": 2,
                "circuit_breaker_cooldown_seconds": 300,
            }
            _record_vision_failure(config, RuntimeError("timed out"))
            self.assertEqual(_get_circuit_breaker_pause_reason(config), "")
            _record_vision_failure(config, RuntimeError("_ssl.c:1112: The handshake operation timed out"))
            self.assertIn("Vision 熔断中", _get_circuit_breaker_pause_reason(config))

    def test_circuit_breaker_resets_after_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "circuit_breaker_file": f"{tmpdir}/breaker.json",
                "circuit_breaker_failures": 1,
                "circuit_breaker_cooldown_seconds": 300,
            }
            _record_vision_failure(config, RuntimeError("timed out"))
            self.assertIn("Vision 熔断中", _get_circuit_breaker_pause_reason(config))
            _record_vision_success(config)
            self.assertEqual(_get_circuit_breaker_pause_reason(config), "")


if __name__ == "__main__":
    unittest.main()

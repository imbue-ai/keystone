from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import sys
import time
from abc import ABC
from abc import abstractmethod
from collections.abc import Callable
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import ContextManager
from typing import Generator

import pytest
from loguru import logger
from playwright._impl._driver import compute_driver_executable
from playwright.sync_api import BrowserContext
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import Playwright
from pydantic import BaseModel
from pydantic import ConfigDict
from pytest_playwright.pytest_playwright import ArtifactsRecorder
from tenacity import retry
from tenacity import retry_if_exception_type
from tenacity import stop_after_delay
from tenacity import wait_fixed

from imbue_core.git import get_git_repo_root
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.playwright_utils import navigate_to_frontend
from sculptor.testing.port_manager import PortManager
from sculptor.testing.port_manager import is_port_free
from sculptor.testing.subprocess_utils import Forwarder


class Frontend(BaseModel, ABC):
    """This class abstracts over the different ways of running a frontend.

    Produced by the frontend_ fixture.
    Used by SculptorFactory to generate a page for testing after launching the backend.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @abstractmethod
    def get_fresh_page(self, request: pytest.FixtureRequest) -> ContextManager[PlaywrightHomePage]:
        """Return a fresh homepage that is ready to run tests on."""


class BrowserFrontend(Frontend):
    backend_port: int

    @contextmanager
    def get_fresh_page(self, request: pytest.FixtureRequest) -> Generator[PlaywrightHomePage, None, None]:
        """When testing against the browser frontend, simply delegate the real work to the "page" fixture provided by
        Playwright.

        Under the hood, this launches a browser per session and creates a new page for each test.
        """
        page: Page = request.getfixturevalue("page")
        _configure_page(page)
        yield navigate_to_frontend(page=page, url=f"http://127.0.0.1:{self.backend_port}")


class DevElectronFrontend(Frontend):
    playwright: Playwright
    backend_port: int
    port_manager: PortManager
    frontend_port: int | None

    @contextmanager
    def get_fresh_page(self, request: pytest.FixtureRequest) -> Generator[PlaywrightHomePage, None, None]:
        """You get one DevElectronFrontend per session/worker.

        It is a lightweight wrapper that you can use to generate a new process for you per test.
        """
        with self.spawn_electron() as cdp_port:
            context, page = _connect_via_cdp(self.playwright, cdp_port)
            with _record_artifacts_for_cdp(context, page, request.getfixturevalue("_artifacts_recorder")):
                yield PlaywrightHomePage(page=page)

    @contextmanager
    def spawn_electron(self) -> Generator[int, None, None]:
        """This function spawns the inner electron process, connects to it, and returns a page."""
        cdp_port = self.port_manager.get_free_port()
        frontend_port = self.frontend_port = self.port_manager.get_free_port()

        cmd = (
            "npm",
            "run",
            "electron:start",
            "--",
            "--",
            "--unhandled-rejections=strict",
            "--trace-warnings",
            f"--remote-debugging-port={cdp_port}",
        )
        env = {
            "SCULPTOR_API_PORT": str(self.backend_port),
            "SCULPTOR_FRONTEND_PORT": str(frontend_port),
        }
        if os.getuid() == 0:
            # We run tests as root on Modal, which requires this flag.
            # Otherwise running Electron fails with
            # "running as root without --no-sandbox is not supported".
            cmd = (*cmd, "--no-sandbox")
        if sys.platform == "linux" and "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
            # Running on Linux without a GUI (like all CI runs) -
            # use Xvfb to make it work.
            cmd = ("xvfb-run", "-e", "/tmp/xvfb-error.log", "-s", "-screen 0 1600x1000x16", *cmd)
        if os.environ.get("IMBUE_MODAL_TEST"):
            # Disable some features when running in Modal sandboxes.
            # Not strictly required, but reduces noise in the logs.
            cmd = (*cmd, "--disable-gpu", "--disable-dev-shm-usage")

        electron_proc = subprocess.Popen(
            cmd,
            cwd=get_git_repo_root() / "sculptor" / "frontend",
            env={**os.environ, **env},
            # Important:
            # Electron Forge's dev frontend is controlled by commands in stdin,
            # and when stdin is closed the frontend will quit.
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=lambda: os.setpgid(0, 0),
        )

        formatted_command = " ".join(cmd)
        formatted_environment = " ".join(f"{k}={v}" for (k, v) in env.items())
        logger.info(
            "Starting Electron frontend with environment and command: `{} {}`",
            formatted_environment,
            formatted_command,
        )
        forwarder = Forwarder(
            electron_proc, prefix="[Electron stdout] ", known_harmless_func=_is_known_harmless_electron_error
        )
        launched = False
        collected_stdout = []
        for line in electron_proc.stdout or []:
            if "Launched Electron app" in line:
                launched = True
                logger.info("Electron frontend launched successfully with CDP port {}", cdp_port)
                forwarder.start()
                break
            else:
                logger.info("[Electron stdout] {}", line)
                collected_stdout.append(line)

        if not launched:
            more_stdout, stderr = electron_proc.communicate()
            # Results from frontend.stdout already have trailing newlines
            stdout = "".join(collected_stdout) + more_stdout
            raise RuntimeError(
                f"Electron frontend failed to start, return code: {electron_proc.returncode}, stdout: {stdout}, stderr: {stderr}"
            )

        stdin = electron_proc.stdin

        try:
            yield cdp_port
        finally:
            logger.debug("Terminating Electron frontend")

            assert stdin, "Electron_procs STDIN must be non-None because the proc was opened with PIPE"
            stdin.close()

            # SIGTERM is the more standard signal for our purpose here, but the Electron Forge dev server doesn't handle it
            # well.
            # Because the test completed, lets kill the electron server and be nice to our coworkers.
            pgid = os.getpgid(electron_proc.pid)
            os.killpg(pgid, signal.SIGINT)
            try:
                # Let's wait to see if it will exit cleanly.
                electron_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.info("Electron frontend did not exit within timeout, killing it")
            finally:
                # Regardless of whether it exited cleanly or not, ensure everyone in the process group is dead.
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    # Process group already exited, which is fine.
                    pass

                assert_eventually(
                    lambda: is_port_free(frontend_port),
                    f":{frontend_port} must be free after the frontend exits",
                )
                self.frontend_port = None

                assert_eventually(lambda: is_port_free(cdp_port), f":{cdp_port} must be free after cdp exits")


def assert_eventually(
    predicate: Callable[[], bool], msg: str, *, timeout_s: float = 30.0, poll_s: float = 0.05
) -> None:
    """Assertion helper that something must be true eventually."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(poll_s)
    raise AssertionError(msg)


class AppElectronFrontend(Frontend):
    """The AppElectronFrontend is used both for direct invocation of the App as well as for the Packaged version of the App."""

    playwright: Playwright
    cdp_port: int

    @contextmanager
    def get_fresh_page(self, request: pytest.FixtureRequest) -> Generator[PlaywrightHomePage, None, None]:
        """The Electron frontend is launched together with the backend in SculptorFactory,
        so we simply connect to it via CDP.
        """
        context, page = _connect_via_cdp(self.playwright, self.cdp_port)
        # Navigate to about:blank first to minimize leakage from the previous test into artifacts.
        navigate_to_frontend(page=page, url="about:blank")
        with _record_artifacts_for_cdp(context, page, request.getfixturevalue("_artifacts_recorder")):
            yield PlaywrightHomePage(page=page)


def dev_electron_frontend(
    port_manager: PortManager,
    backend_port: int,
    playwright: Playwright,
) -> DevElectronFrontend:
    """Assigns ports, creates a new wrapper around the frontend, and then returns."""
    return DevElectronFrontend(
        playwright=playwright, backend_port=backend_port, port_manager=port_manager, frontend_port=None
    )


def _is_known_harmless_electron_error(line: str) -> bool:
    return (
        # This appears on macOS
        "Keychain lookup for suffixed key failed:" in line
        or
        # These appear on Linux when running without a full desktop
        "Failed to connect to the bus:" in line
        or "Could not bind NETLINK socket" in line
        or "Failed to read /proc/sys/fs/inotify/max_user_watches" in line
        or "X connection error received." in line
    )


def _connect_via_cdp(playwright: Playwright, cdp_port: int) -> tuple[BrowserContext, Page]:
    """Connect to our existing frontend system via the Chrome DevTools Protocol.

    When this function is called, the frontend may not actually be ready to accept CDP connections yet, so we need to
    retry.
    """
    retry_connect = retry(
        stop=stop_after_delay(120),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(PlaywrightError),
        reraise=True,
    )(lambda: playwright.chromium.connect_over_cdp(f"http://localhost:{cdp_port}"))
    browser = retry_connect()
    logger.info("Connected to Electron frontend over CDP")
    assert len(browser.contexts) == 1, "Expected exactly one context"
    context = browser.contexts[0]
    pages = [page for page in context.pages if not page.url.startswith("devtools://")]
    assert len(pages) == 1, f"Expected exactly one non-devtools page, got {pages}"
    page = pages[0]
    _configure_page(page)
    return context, page


@contextmanager
def _record_artifacts_for_cdp(
    context: BrowserContext, page: Page, artifacts_recorder: ArtifactsRecorder
) -> Generator[None, None, None]:
    """Mimics Playwright's artifact generation functionality for CDP connections.

    There are three types of artifacts that we want to record: traces, screenshots, and videos.
    The first two are completely taken care of by ArtifactsRecorder,
    so we just manually call its lifecycle callbacks.

    Video recording has to be manually re-implemented.

    Videos are a bit more complex:
    the actual recording is done in Playwright's NodeJS side,
    and the Python side exposes a handle to the video on page.video,
    and that's what ArtifactsRecorder looks for.

    However, page.video is is not populated for CDP-connected pages.
    So we implement our own recording functionality using the CDP screencast API,
    and save it to the same location that Playwright would save it to;
    we also have to implement our own handling of Playwright --video flag.
    """

    artifacts_recorder.on_did_create_browser_context(context)
    try:
        with _record_video_for_cdp(context, page, artifacts_recorder):
            yield
    finally:
        artifacts_recorder.on_will_close_browser_context(context)


@contextmanager
def _record_video_for_cdp(
    context: BrowserContext, page: Page, artifacts_recorder: ArtifactsRecorder
) -> Generator[None, None, None]:
    """In Playwright,
    the actual video recording is done in Playwright's NodeJS side,
    but that code path is not run for CDP-connected pages.

    So we implement our own recording functionality using the CDP screencast API,
    and save it to the same location that Playwright would save it to;
    we also have to implement our own handling of Playwright --video flag.
    """
    video_flag = artifacts_recorder._pytestconfig.getoption("--video")
    if video_flag == "off":
        yield
        return

    output_path = Path(artifacts_recorder._output_path)
    output_path.mkdir(parents=True, exist_ok=True)
    # Emulate Playwright's naming scheme for videos:
    # - video.webm if there's only one
    # - video-1.webm, video-2.webm, etc. otherwise
    if (output_path / "video.webm").exists():
        # We're here after the first video has been recorded.
        (output_path / "video.webm").rename(output_path / "video-1.webm")
        video_output = output_path / "video-2.webm"
    elif (output_path / "video-1.webm").exists():
        # We're here after the second video has been recorded.
        suffixes = (video.stem.split("-")[1] for video in output_path.glob("video-*.webm"))
        numbers = (int(suffix) for suffix in suffixes if suffix.isdigit())
        last_number = max(numbers)
        video_output = output_path / f"video-{last_number + 1}.webm"
    else:
        # We're the first.
        video_output = output_path / "video.webm"

    # The actual FPS is not known until we start recording,
    # but we have to pass something to ffmpeg.
    # This is what Playwright uses too.
    fps = 25
    # Keep these in sync with electron/main.ts.
    width = 1600
    height = 1000
    # The flags are from Playwright:
    # https://github.com/microsoft/playwright/blob/eed1f19104886e76c1bb1cb99dff67d88a252eaa/packages/playwright-core/src/server/chromium/videoRecorder.ts#L93
    ffmpeg_cmd = [
        _find_playwright_ffmpeg(),
        "-loglevel",
        "error",
        "-f",
        "image2pipe",
        "-avioflags",
        "direct",
        "-fpsprobesize",
        "0",
        "-probesize",
        "32",
        "-analyzeduration",
        "0",
        "-c:v",
        "mjpeg",
        "-i",
        "pipe:0",
        "-y",
        "-an",
        "-r",
        str(fps),
        "-c:v",
        "vp8",
        "-qmin",
        "0",
        "-qmax",
        "50",
        "-crf",
        "8",
        "-deadline",
        "realtime",
        "-speed",
        "8",
        "-b:v",
        "1M",
        "-threads",
        "1",
        "-vf",
        f"pad={width}:{height}:0:0:gray,crop={width}:{height}:0:0",
        video_output,
    ]
    ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)

    cdp = context.new_cdp_session(page)

    def handle_screencast_frame(params):
        session_id = params["sessionId"]
        jpg_bytes = base64.b64decode(params["data"])
        try:
            ffmpeg_proc.stdin.write(jpg_bytes)
        except Exception as ex:
            logger.error("Failed to write frame to ffmpeg: {}", ex)
        cdp.send("Page.screencastFrameAck", {"sessionId": session_id})

    cdp.on("Page.screencastFrame", handle_screencast_frame)

    cdp.send(
        "Page.startScreencast",
        {
            "format": "jpeg",
            "quality": 90,
            "maxWidth": width,
            "maxHeight": height,
            "everyNthFrame": 1,  # Capture every frame
        },
    )

    try:
        yield
    finally:
        cdp.send("Page.stopScreencast")
        cdp.detach()
        ffmpeg_proc.stdin.close()
        should_keep_video = True
        if video_flag == "retain-on-failure":
            request = artifacts_recorder._request
            failed = request.node.rep_call.failed if hasattr(request.node, "rep_call") else True
            should_keep_video = failed
        try:
            ffmpeg_proc.wait(timeout=30)
        except Exception as ex:
            ffmpeg_proc.kill()
            if should_keep_video:
                logger.error("ffmpeg did not terminate within timeout: {}", ex)
            else:
                # Keep the noise low since we don't need the video anyway.
                logger.info("ffmpeg did not terminate within timeout: {}", ex)
        if not should_keep_video:
            Path(video_output).unlink(missing_ok=True)


@lru_cache()
def _find_playwright_ffmpeg() -> str:
    """Find the ffmpeg binary installed by Playwright by running a NodeJS snippet.

    This is a hack as it relies on Playwright's internal code,
    but it's also the most reliable way to get the ffmpeg binary that Playwright itself uses.
    """
    playwright_node = compute_driver_executable()[0]
    cmd = [
        playwright_node,
        "--print",
        "JSON.stringify(require('./package/lib/server/registry').registry.findExecutable('ffmpeg').executablePath())",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=os.path.dirname(playwright_node),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to find Playwright's ffmpeg binary: {result.stderr}")
    return json.loads(result.stdout)


def _configure_page(page: Page) -> None:
    page.set_default_timeout(4 * 60 * 1000)
    # aad7cde6-a1ff-440e-a7c9-209272d31dc8:
    # A bug since Playwright 1.53.0 causes the NodeJS driver to crash
    # when a dialog is shown from an Electron app:
    # https://github.com/microsoft/playwright/issues/36627
    # Since the Playwright Python binding can't really handle the driver crashing,
    # this will manifest in the test hanging forever.
    #
    # Fortunately, we don't really rely on JavaScript dialogs in our UI,
    # but there are cases that I don't fully understand that will cause a beforeunload dialog to be shown;
    # look for the UUID for more details.
    #
    # For another reason I don't fully understand,
    # simply registering a handler for this CDP event works around this issue.
    page.on("dialog", lambda dialog: logger.info(f"Dialog shown: {dialog}"))

> This doc is incomplete.
>
> TODO(Qi): Make it more complete

# Testing Sculptor

## Running tests against Electron locally

### Running with Xvfb on Linux

Since Electron doesn't have a "headless" mode that browsers have,
running Electron tests will open Electron windows on your desktop.

On Linux, you can avoid this by using [Xvfb](https://en.wikipedia.org/wiki/Xvfb),
which is a X display server that outputs to a virtual framebuffer.

As long as you have `xvfb` installed,
simply unset `DISPLAY` when you run the tests,
and our test setup code will automatically run the Electron frontend with Xvfb.

### Scaling the Electron window

Some of our tests assume a fairly large viewport,
and don't handle alternative layouts when the viewport is smaller.
This can be a problem when running tests against Electron,
since the Electron window's size is constrained by the monitor size,
so some of the tests will fail if your Electron window is opened on a small monitor.

We should fix all those tests (look for `FIXME` in the tests),
but before that has happened,
you can work around this by setting `SCULPTOR_ZOOM_FACTOR` when running the test,
which scales the size of the content in the window.

As an example,
setting `SCULPTOR_ZOOM_FACTOR` to 0.5 makes the content 50% the normal size,
thus making the viewport 2x wider and taller.

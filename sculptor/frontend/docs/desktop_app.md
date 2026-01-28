# Desktop app

We package Sculptor into a desktop app using [Electron Forge](https://www.electronforge.io/).

## Building prerequisite assets

This is a prerequisite for the following targets.
It requires [ImageMagick](https://imagemagick.org/).

```bash
just refresh
```

## Running development version

```bash
just start
```

If only code in the `frontend` directory has changed, you can also just do:

```bash
cd frontend
npm run electron:start
```

## Packaging and making

Electron Forge uses the words "packaging" and "making" in very specific ways.
As an example for macOS, "packaging" means creating an `.app` file,
while "making" means putting that `.app` file in a `.dmg` installer.
See the official [documentation](https://www.electronforge.io/core-concepts/build-lifecycle).

```bash
just app
just pkg
```

## Passing extra arguments

When running the desktop app binary,
you can pass extra arguments to the Sculptor backend by prefixing them with `--sculptor=`.
This is mostly useful in manual testing.

To pass multiple arguments, prefix all of them with `--sculptor=` (**not** merge them into one space-separated argument):

```bash
./frontend/out/sculptor-darwin-arm64/sculptor.app/Contents/MacOS/sculptor --sculptor=--foo --sculptor=--bar
# NOT this:
./frontend/out/sculptor-darwin-arm64/sculptor.app/Contents/MacOS/sculptor --sculptor='--foo --bar'
```

When using `npm run electron:start` to pass extra arguments,
you also need **two** `--` arguments:

```bash
cd frontend
npm run electron:start -- -- --sculptor='--foo --bar'
```

The first `--` tells `npm run` that the rest is the arguments to pass to the `electron-forge start` command;
the second `--` tells the `electron-forge start` command that the rest is the arguments to pass to the binary.

## Running multiple instances of the packaged app

<!-- 74643a8d-5e1d-4b5d-9b36-62cafce687ca -->

We prevent users from running multiple instances of the packaged app,
since it leads to race conditions in accessing shared resources like the database.
This restriction doesn't apply for unpackaged apps.

When testing, if you do need to run multiple instances of the packaged app,
you can run them with different values of `SCULPTOR_USER_DATA_DIR` and `SCULPTOR_FOLDER`:

```sh
# on Linux:
env SCULPTOR_USER_DATA_DIR=$HOME/sculptor-data-1 SCULPTOR_FOLDER=$HOME/sculptor-1 ./Sculptor.AppImage
# or on macOS:
env SCULPTOR_USER_DATA_DIR=$HOME/sculptor-data-1 SCULPTOR_FOLDER=$HOME/sculptor-1 open -n Sculptor.app
```

(This works because the single-instance check is based on locking a file under the user data directory.)

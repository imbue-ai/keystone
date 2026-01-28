# Sculptor's Docker Images

## Rebuilding the Modal Docker Image (Dockerfile.slim)

We cache the base Modal image, and pin a reference to the image id in our code. If you update the Dockerfile.slim, you must a) build the image and b) update
the pin, so that modal knows to use it.

From a clean commit, you may run:

```
uv run cli refresh-image
```

This will update the pinned file in-place, so that you can commit the change.


## Building Dockerfile.slim locally

Unclear why you might want to do this, but if you do:

Example:
```sh
docker build
    --platform linux/amd64 \
    -f sculptor/docker/Dockerfile.slim \
    .
```
* Typically only built on Modal.
* It only works on amd64.
* Takes about 35 minutes.

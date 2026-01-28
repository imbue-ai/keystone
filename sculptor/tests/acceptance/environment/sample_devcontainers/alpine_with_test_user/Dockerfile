FROM alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1

RUN apk update
RUN apk add curl

# Create a test user
RUN adduser -D test_user

RUN touch /alpine_test_user.txt

# Set up working directory
WORKDIR /workspace

# End as test_user (no remoteUser in devcontainer.json)
USER test_user

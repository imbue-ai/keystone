FROM alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1

RUN apk update
RUN apk add curl

# Create a test user
RUN adduser -D test_user

RUN touch /alpine_test_user_json.txt

# Set up working directory
WORKDIR /workspace

# Stay as root in Dockerfile, but devcontainer.json will specify test_user

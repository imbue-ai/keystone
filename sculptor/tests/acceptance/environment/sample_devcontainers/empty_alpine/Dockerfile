FROM alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1

RUN apk update
RUN apk add curl

RUN touch /empty_alpine.txt

# Set up working directory
WORKDIR /workspace

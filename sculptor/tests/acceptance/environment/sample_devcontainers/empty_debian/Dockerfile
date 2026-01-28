FROM debian:12.12@sha256:26f2a7cab45014541c65f9d140ccfa6aaefbb49686c6759bea9c6f7f5bb3d72f

RUN touch /empty_debian.txt

RUN apt-get update
RUN apt-get install -y curl
RUN which curl

# Set up working directory
WORKDIR /workspace

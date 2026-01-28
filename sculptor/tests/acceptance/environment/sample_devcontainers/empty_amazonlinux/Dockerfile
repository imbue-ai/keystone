FROM amazonlinux:latest

RUN touch /empty_amazonlinux.txt

RUN yum update -y
RUN yum install -y python3

# Set up working directory
WORKDIR /workspace

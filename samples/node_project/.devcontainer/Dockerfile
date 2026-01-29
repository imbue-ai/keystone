# Use official Node.js LTS image
FROM node:20-slim

# Create test artifacts directory
RUN mkdir -p /test_artifacts && chmod 777 /test_artifacts

# Set working directory
WORKDIR /project_src

# Copy package.json first for better caching (build context is now workspace root)
COPY package.json ./package.json

# Install dependencies
RUN npm install

# Copy the rest of the project files
COPY app.js ./app.js
COPY app.test.js ./app.test.js

# Copy the test script from .devcontainer subdirectory
COPY .devcontainer/run_all_tests.sh ./.devcontainer/run_all_tests.sh

# Ensure the user has proper permissions
RUN chown -R node:node /project_src /test_artifacts

USER node

# Set the working directory
WORKDIR /project_src

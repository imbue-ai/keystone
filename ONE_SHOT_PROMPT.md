In this project, called bootstrap\_devcontainer, we are going to build a Python typer CLI that takes as input a source tree from an existing software project (think: you just cloned a github repo), and crafts an appropriate dev container and Dockerfile in which that project's test suite runs successfully.

It's going to work by invoking a coding agent CLI in autonomous mode with a prompt instructing it to crank until it gets this working \-- the default should be to run claude code with –dangerously-skip-permissions (or, if you can get it to work, allow it to run the devcontainer and docker binaries only).

The coding agent should be prompted to:

1. Create a //.devcontainer/devcontainer.json file at the project root (// is the project root).  
2. Create a //.devcontainer/Dockerfile alongside that.  
3. Create a run\_all\_tests.sh script alongside the Dockerfile  
   1. run\_all\_tests.sh should take an arg called \--test\_artifact\_dir  
   2. It should return 0 (success) IFF all tests pass and forward enough information to stdout/stderr to enable debugging failing tests.  
   3. test\_artifact\_dir should be populated with artifacts from running the tests that prove the ran: JSON reports, coverage reports, etc.  For now, it can be organized loosely, but with a few requests:  
      1. For each command run, create a subdirectory with a good “name” for that command.  
      2. In that directory, put files called stdout.txt and stderr.txt, ideally with each line prefixed by a timestamp so that they can be interleaved if desired.  
      3. (So that the agent can also receive these outputs on the stdout/stderr of run\_all\_tests.sh as run via \`docker run IMAGE /project\_src/.devcontainer/run\_all\_tests.sh, it might be a good idea to tee the outputs.)  
      4. For python code, there should be an aggregated JSON report in pytest-json-report format in test\_artifact\_dir/pytest-json-report.json  
      5. A file called final\_result.json, which says whether the tests passed or failed.  
4. Inside the Dockerfile, COPY the input source tree (which now includes the devcontainer config)  into the docker image to /project\_src as a penultimate step. (no volume mounts – the image should stand alone)  
5. The Dockerfile should leave the CWD as /project\_src to facilitate the rest of the process.

Some notes:

* The only changes the agent should make to the code tree are in the //.devcontainer/… subtree.  
* The built image only needs to be stored locally.  
* When running the tests inside the image, both the agent and our bootstrap\_devcontainer project should use \`docker run\` and may bind mount a temporary directory to use for test\_artifact\_dir, so that the agent can later inspect testing artifacts for debugging purposes.  (actually the Python typer CLI bootstrap\_devcontainer.py should take this “scratch space” directory as an argument).  
* All relevant output files from run\_all\_tests.sh should be channeled into test\_artifact\_dir

The agent should make sure that all the tests are run for polyglot projects (backend, frontend, E2E).  For frontend projects, this might require installing node, Xvfb or other playwright dependencies.

To test whether its output is correct, the agent should:

1. Build the image using \`devcontainer build \--workspace-folder .\`  
2. \`docker run IMAGE ./.devcontainer/run\_all\_tests.sh\` in the image and check its return code, and that the tests are actually being run.  
3. Examine the contents of test\_artifact\_dir

The agent should not stop until:

1. It has a buildable Dockerfile.  
2. With a run\_all\_tests.sh that succeeds and runs as many tests as possible, with tests that cannot be made to work by environment changes disabled.  The agent should not edit the code repository outside of the //.devcontainer folder.  If tests simply can’t be made to work, they should be turned off using command line arguments to the test harness.

The agent’s work will be scored on:

1. How many of the tests it can get working via run\_all\_tests.  
2. The overall amount of time it takes for the agent to do this task of setting up the devcontainer.  
   1. To make devcontainer setup efficient, the agent should:  
      1. Optimize the Dockerfile in stages so that subsequent attempts are faster.  
      2. Run parts of the test suites in parallel if feasible without too much complexity.

A special note on testing and timeouts:

* Failing test suites often “get stuck”, for example waiting for conditions that will never happen.  
* To avoid this, and since the agent will be judged on how long it took to finish the task of constructing the devcontainer, the agent should be prompted to somehow closely monitor test runs to avoid waiting on stuck tests.  
* One way to do this is the linux command wrapper \`timeout\`.  There’s a goldilocks value: too short and you churn, too long, and it doesn’t help.  
* It may also be beneficial to attempt running the tests in parallel, either from run\_all\_tests.sh or directly run parts of the test suite to debug, using commands like \`docker run IMAGE\_NAME “pytest some/subtree”\`

Our bootstrap\_devcontainer.py project should:

* Use portable modern python configured with uv.  
* Be relatively lightweight: allowed deps: uv, typer, pytest  
* Assume that the devcontainer and docker CLIs are already on the path, and the docker daemon is running.  
* Measure the total time and token spending (input, cached, and output) that the autonomous agent needed to get the job done, and its success.  This output should be emitted as JSON on stdout.  
* Emit its own logs on stderr.  
* Actually check that once the agent finishes, building the image with \`devcontainer build \--workspace-folder .\` actually works, and that running the //.devcontainer/run\_all\_tests.sh   
* Include a small sample python repo and an E2E test that points the CLI at it to create a devcontainer.  This will be an expensive test to run and that’s OK – let’s mark it as manual.


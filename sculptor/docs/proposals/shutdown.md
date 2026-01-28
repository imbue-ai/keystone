# Shutdown

## Goal of this proposal

We want to know how to approach orderly application shutdown, resource cleanups, restarts and what role do Concurrency Groups play in this.
Some practical considerations are listed [here](https://www.dropbox.com/scl/fi/n46xlbf9u5o496edvkel6/Correct-startup-and-shutdown.paper?rlkey=35ahsb7ojqa87unb0jciipb7e&dl=0).

## Constraints

We want shutdown to:

- respect dependencies between components
- be as time-efficient (= parallel) as possible
- be robust to exceptions in various parts of the application
- eventually even respect dependencies and shutdown order inside containers

We also want:

- to be able to recover from unclean shutdowns (e.g. from sigkill)
- subsequent application startup to be as fast as possible
- to be able to measure and communicate shutdown progress


## Current state

Right now, shutdown happens somewhat spontaneously:

- We tear down Sculptor services in an order that respects dependencies.
- But we also seem to immediately propagate termination signals to subprocesses, including container processes, at least sometimes.
- Which means that some components start to wind down regardless of whether something depends on them or not.

This mostly works out in practice for various reasons but is a little hard to further build upon.


## Vision

If possible, termination signals will be intercepted and a top-down shutdown will be initiated instead in response.

The top-down shutdown will gradually propagate through the component tree in the correct order. A parent component should know the dependencies among its child components and shut them down in the right order and with the right parallelism. The precise mechanism of shutting down a particular component depends on circumstances - it could be setting a shutdown event, it could be calling a `stop()` method, or it could be something else. In other words, we will not maintain an explicit unified structure for this purpose. The "root" component that initiates this shutdown is the web app through its lifespan routine.

We will extensively use context managers for emergency cleanups during disorderly shutdowns caused by runtime exceptions.

An indicator of a previously clean shutdown will be placed in the sculptor folder. Next time we start up, we will use it to determine if we need to recover from disorderly shutdown (e.g. by cleaning old containers). Deleting the old indicator will be the first thing the app does when starting.

Eventually, we will introduce a lifecycle tracking component that will collect lifecycle events via callbacks, allowing us to measure how long components take to start or stop and to communicate startup or shutdown progress to the user if desired.

(Note: some of the above is already in place!)


### The role of ConcurrencyGroups

We will keep ConcurrencyGroups responsible for the management of concurrency units. There was an alternative vision in which ConcurrencyGroups assumed more responsibilities and their tree was the primary means through which shutdown was orchestrated but that vision didn't survive closer scrutiny.

Some additional points related to recent discussions about `ConcurrencyGroup.shutdown()`:

- It would be surprising if `ConcurrencyGroup.shutdown()` didn't actually send the shutdown event to its strands. So we'll continue doing that.
- This means that we shouldn't initiate global shutdown by calling `root_concurrency_group.shutdown()` because that would immediately start killing strands in children without a particular order.
- Instead, we'll continue orchestrating the orderly shutdown through other means (APP.shutdown_event, ServiceCollection.stop_all()) with `ConcurrencyGroup.shutdown()` just being a tool that the relevant components can optionally call.
- When a concurrency group is shutting down, for simplicity, we will continue not allowing new strands to be created.
- If a new strand is needed as part of the shutdown logic, it needs to be created through the parent concurrency group, not the one that's currently undergoing shutdown.


## Next steps

1. (if not too hard) Transform services into context managers so that it's easier to tie their and their concurrency groups' lifecycles and to implement emergency cleanups.
2. See if we can mask SIGINT signals in subprocesses. Instead, implement the voluntary top-down shutdown in response to the signal.
    - (For the most part, this should hopefully already work.)
3. Add component lifecycle tracking to give us visibility into what's going on for potential startup / shutdown optimizations.
4. Do everything else that is mentioned in the [shutdown doc](https://www.dropbox.com/scl/fi/n46xlbf9u5o496edvkel6/Correct-startup-and-shutdown.paper?rlkey=35ahsb7ojqa87unb0jciipb7e&dl=0).
5. Add the clean shutdown indicator and optionally skip unnecessary cleanups at startup.

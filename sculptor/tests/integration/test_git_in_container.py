from sculptor.services.environment_service.environments.docker_environment import DockerEnvironment
from sculptor.tasks.handlers.run_agent.git import run_git_command_in_environment


def test_run_git_command_with_large_output(docker_environment: DockerEnvironment) -> None:
    run_git_command_in_environment(
        environment=docker_environment,
        command=["bash", "-c", "yes 'a very long output' | head -n 100000"],
        secrets={},
        timeout=10.0,
    )

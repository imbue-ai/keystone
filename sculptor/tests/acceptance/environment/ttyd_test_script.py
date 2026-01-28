from imbue_core.agents.data_types.ids import ProjectID
from sculptor.interfaces.environments.base import ModalEnvironmentConfig
from sculptor.interfaces.environments.base import TTYD_SERVER_PORT
from sculptor.interfaces.environments.constants import CONTAINER_SSH_PORT
from sculptor.services.environment_service.providers.modal.environment_utils import build_modal_environment
from sculptor.services.environment_service.providers.modal.image_utils import build_modal_image

if __name__ == "__main__":
    test_modal_app_name = "test-ttyd"
    relative_file_path = (
        "/Users/amyhu/generally_intelligent/sculptor/tests/acceptance/environment/Dockerfile.test.ttyd"
    )
    image = build_modal_image(relative_file_path, test_modal_app_name, project_id=ProjectID())
    environment = None
    try:
        environment = build_modal_environment(
            image,
            config=ModalEnvironmentConfig(unencrypted_ports=[CONTAINER_SSH_PORT, TTYD_SERVER_PORT]),
            project_id=image.project_id,
        )
        assert environment.sandbox is not None
        tunnels = environment.sandbox.tunnels()
        ttyd_tunnel = tunnels[TTYD_SERVER_PORT]
        ttyd_host, ttyd_port = ttyd_tunnel.tcp_socket
        print(f"ttyd_host: {ttyd_host}, ttyd_port: {ttyd_port}")
        claude_command = ["tmux", "new-session", "-d", "-s", "session", "claude"]
        # Note: this method is not implemented yet so this test will always fail for now.
        claude_tmux_process = environment.run_process_in_background(claude_command, secrets={})
        claude_tmux_process.wait()
        ttyd_command = ["ttyd", "-p", str(TTYD_SERVER_PORT), "-W", "-o", "tmux", "a", "-t", "session"]
        process = environment.run_process_in_background(ttyd_command, secrets={})
        process.wait()
    finally:
        if environment is not None:
            environment.close()

from sculptor.web.access_log_filter import is_frequently_polled_route
from sculptor.web.access_log_filter import should_suppress_access_log


def test_static_route_match() -> None:
    message = '127.0.0.1:63270 - "GET /api/sync/global_singleton_state HTTP/1.1" 200'
    assert is_frequently_polled_route(message) is True


def test_regex_route_match() -> None:
    message = '127.0.0.1:63270 - "GET /api/v1/projects/prj_05e1142e0f1e5887fb54d58e33/repo_info HTTP/1.1" 200'
    assert is_frequently_polled_route(message) is True


def test_non_matching_route() -> None:
    message = '127.0.0.1:63270 - "GET /api/v1/tasks HTTP/1.1" 200'
    assert is_frequently_polled_route(message) is False


def test_does_not_overmatch_similar_routes() -> None:
    # Should not match routes that contain the pattern but aren't the exact endpoint
    assert is_frequently_polled_route('GET /api/v1/health_check HTTP/1.1" 200') is False
    assert is_frequently_polled_route('GET /api/v1/projects/prj_123/repo_info_extended HTTP/1.1" 200') is False
    assert is_frequently_polled_route('GET /api/v1/projects/prj_123/other HTTP/1.1" 200') is False


def test_should_suppress_only_healthy_polling_routes() -> None:
    message = '127.0.0.1:63270 - "GET /api/v1/health HTTP/1.1" 200'
    assert should_suppress_access_log(message) is True


def test_should_not_suppress_failures_even_on_polled_routes() -> None:
    message = '127.0.0.1:63270 - "GET /api/v1/health HTTP/1.1" 500'
    assert should_suppress_access_log(message) is False


def test_should_not_suppress_other_routes() -> None:
    message = '127.0.0.1:63270 - "GET /api/v1/tasks HTTP/1.1" 200'
    assert should_suppress_access_log(message) is False

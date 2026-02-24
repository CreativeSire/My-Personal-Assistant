from security_scopes import (
    check_scope_allowed,
    parse_scope_map_env,
    required_scope_for_route,
)


def test_required_scope_for_route_basics():
    assert required_scope_for_route("/v1/tasks", "GET") == "tasks.read"
    assert required_scope_for_route("/v1/tasks", "POST") == "tasks.write"
    assert required_scope_for_route("/v1/training/export", "POST") == "training.write"
    assert required_scope_for_route("/v1/audit/auth_blocks", "GET") == "audit.read"


def test_scope_map_parser_and_enforcement():
    scope_map = parse_scope_map_env("primary:all,viewer:tasks.read|metrics.read|audit.read")
    allowed, _ = check_scope_allowed("primary", "tasks.write", scope_map)
    assert allowed
    allowed, _ = check_scope_allowed("viewer", "tasks.read", scope_map)
    assert allowed
    denied, _ = check_scope_allowed("viewer", "tasks.write", scope_map)
    assert not denied


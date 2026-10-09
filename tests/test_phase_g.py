"""Tests for Phase G — Host Integration (slices 41–46).

Covers:
    41. hlidskjalf.host.mcp         — MCP JSON-RPC client + MockMCPServer
    42. hlidskjalf.host.harvester   — host.state envelope
    43. hlidskjalf.host.transport   — InMemoryTransport pair, HttpTransport stub
    44. hlidskjalf.host.sagnaskemma — PartyState -> vitals viewport state
    45. hlidskjalf.host.divination  — mock ephemeris, tarot, runes
    46. hlidskjalf.host.tools       — ToolRegistry over MCP
"""

import pytest

from hlidskjalf.host import divination, harvester, mcp, sagnaskemma, transport
from hlidskjalf.host.mcp import MCPClient, MCPError, MockMCPServer, make_pipe_pair
from hlidskjalf.host.sagnaskemma import (
    PartyMember,
    PartyState,
    member_from_dict,
    party_from_dict,
)
from hlidskjalf.host.tools import ToolInvocationError, ToolRegistry
from hlidskjalf.host.transport import (
    HttpTransport,
    InMemoryTransport,
    TransportError,
)


# ---------------------------------------------------------------------------
# Slice 41 — MCP client
# ---------------------------------------------------------------------------


@pytest.fixture()
def mcp_pair():
    server = MockMCPServer()
    server.register_tool("ping", lambda args: {"pong": True}, description="Ping")
    server.register_tool(
        "add",
        lambda args: args["a"] + args["b"],
        description="Add two numbers",
    )
    client, server = make_pipe_pair(server)
    yield client, server
    client.close()
    server.stop()


def test_mcp_list_tools(mcp_pair):
    client, _ = mcp_pair
    tools = client.list_tools()
    names = {tool["name"] for tool in tools}
    assert {"ping", "add"} <= names
    for tool in tools:
        assert "name" in tool and "description" in tool and "inputSchema" in tool


def test_mcp_call_tool(mcp_pair):
    client, _ = mcp_pair
    result = client.call_tool("ping", {})
    assert result["isError"] is False
    assert result["content"][0]["text"] == '{"pong": true}'


def test_mcp_call_tool_with_args(mcp_pair):
    client, _ = mcp_pair
    result = client.call_tool("add", {"a": 2, "b": 40})
    assert result["isError"] is False
    assert result["content"][0]["text"] == "42"


def test_mcp_call_unknown_tool_raises(mcp_pair):
    client, _ = mcp_pair
    with pytest.raises(MCPError):
        client.call_tool("nope", {})


def test_mcp_tool_handler_exception_surfaces_as_is_error(mcp_pair):
    client, server = mcp_pair
    server.register_tool("boom", lambda args: 1 / 0)
    result = client.call_tool("boom", {})
    assert result["isError"] is True
    assert "ZeroDivisionError" in result["content"][0]["text"]


def test_mcp_mock_server_tool_descriptors():
    server = MockMCPServer()
    server.register_tool(
        "weather",
        lambda args: {"sky": "grey"},
        description="Sky report",
        input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
    )
    client, server = make_pipe_pair(server)
    try:
        (descriptor,) = client.list_tools()
        assert descriptor["name"] == "weather"
        assert descriptor["description"] == "Sky report"
        assert descriptor["inputSchema"]["properties"]["city"]["type"] == "string"
    finally:
        client.close()
        server.stop()


# ---------------------------------------------------------------------------
# Slice 42 — harvester
# ---------------------------------------------------------------------------


def test_harvest_envelope_shape():
    envelope = harvester.harvest(top_n=3, sample_interval=0.01)
    assert envelope["v"] == 1
    assert envelope["type"] == "host.state"
    assert isinstance(envelope["ts"], float)
    payload = envelope["payload"]
    assert set(payload) == {"host", "timestamp", "memory", "disks", "processes"}


def test_harvest_host_info():
    info = harvester.host_info()
    assert info["hostname"]
    assert info["os"]
    assert info["python"]


def test_harvest_memory_info():
    memory = harvester.memory_info()
    assert memory["total_bytes"] > 0
    assert memory["used_bytes"] >= 0
    assert 0.0 <= memory["percent"] <= 100.0


def test_harvest_disk_info():
    disks = harvester.disk_info()
    assert len(disks) >= 1
    for disk in disks:
        assert disk["total_bytes"] > 0
        assert 0.0 <= disk["percent"] <= 100.0


def test_harvest_top_processes_ranked():
    processes = harvester.top_processes(top_n=5, sample_interval=0.01)
    assert 1 <= len(processes) <= 5
    for proc in processes:
        assert isinstance(proc["pid"], int)
        assert proc["name"]
        assert proc["cpu"] >= 0.0
    cpus = [proc["cpu"] for proc in processes]
    assert cpus == sorted(cpus, reverse=True)


# ---------------------------------------------------------------------------
# Slice 43 — transport
# ---------------------------------------------------------------------------


def test_inmemory_pair_bidirectional():
    a, b = InMemoryTransport.create_pair()
    try:
        a.send({"hello": "from-a"})
        assert b.recv(timeout=2) == {"hello": "from-a"}
        b.send({"hello": "from-b"})
        assert a.recv(timeout=2) == {"hello": "from-b"}
    finally:
        a.close()
        b.close()


def test_inmemory_recv_timeout():
    a, b = InMemoryTransport.create_pair()
    try:
        with pytest.raises(TimeoutError):
            a.recv(timeout=0.05)
    finally:
        a.close()
        b.close()


def test_inmemory_send_after_close_raises():
    a, b = InMemoryTransport.create_pair()
    a.close()
    with pytest.raises(TransportError):
        a.send({"x": 1})
    assert not a.is_connected
    assert b.is_connected
    b.close()


def test_http_transport_stub_connect_fails_cleanly():
    t = HttpTransport("http://127.0.0.1:1/none", max_attempts=2, base_delay=0.01)
    with pytest.raises(TransportError) as exc_info:
        t.connect()
    assert t.attempts == 2
    assert t.state == HttpTransport.DISCONNECTED
    assert not t.is_connected
    assert "stub" in str(exc_info.value).lower() or "no real server" in str(
        exc_info.value
    )


def test_http_transport_backoff_delay_grows():
    t = HttpTransport(
        "http://127.0.0.1:1/none", base_delay=0.5, max_delay=1.0, jitter=0.0
    )
    d1 = t._backoff_delay(1)
    d2 = t._backoff_delay(2)
    d3 = t._backoff_delay(10)
    assert d1 == 0.5
    assert d1 < d2 == 1.0 == d3  # exponential growth, capped at max_delay


def test_http_transport_send_requires_connection():
    t = HttpTransport("http://127.0.0.1:1/none")
    with pytest.raises(TransportError):
        t.send({"x": 1})
    t.close()  # idempotent close


# ---------------------------------------------------------------------------
# Slice 44 — sagnaskemma adapter
# ---------------------------------------------------------------------------


@pytest.fixture()
def party():
    return PartyState(
        [
            PartyMember(name="Volmarr", hp=42, max_hp=50),
            PartyMember(name="Yrsa", hp=30, max_hp=30, conditions=["blessed"]),
        ]
    )


def test_party_damage_and_down(party):
    member = party.damage("Volmarr", 10)
    assert member.hp == 32
    party.damage("Volmarr", 99)
    assert party.get("Volmarr").hp == 0
    assert party.get("Volmarr").status == "down"


def test_party_heal_revives(party):
    party.damage("Yrsa", 30)
    assert party.get("Yrsa").status == "down"
    party.heal("Yrsa", 5)
    assert party.get("Yrsa").hp == 5
    assert party.get("Yrsa").status == "alive"
    party.heal("Yrsa", 999)
    assert party.get("Yrsa").hp == 30  # capped at max_hp


def test_party_conditions_and_status(party):
    party.add_condition("Volmarr", "poisoned")
    party.add_condition("Volmarr", "poisoned")  # idempotent
    assert party.get("Volmarr").conditions == ["poisoned"]
    party.remove_condition("Volmarr", "poisoned")
    assert party.get("Volmarr").conditions == []
    party.set_status("Volmarr", "stable")
    assert party.get("Volmarr").status == "stable"


def test_party_roster_rules(party):
    with pytest.raises(ValueError):
        party.add_member(PartyMember(name="Volmarr", hp=1, max_hp=5))
    with pytest.raises(KeyError):
        party.get("Nobody")
    removed = party.remove_member("Yrsa")
    assert removed.name == "Yrsa"
    assert [m.name for m in party.members] == ["Volmarr"]


def test_to_viewport_state_format(party):
    state = party.to_viewport_state()
    assert set(state) == {"members"}
    assert len(state["members"]) == 2
    volmarr = next(m for m in state["members"] if m["name"] == "Volmarr")
    assert volmarr == {
        "name": "Volmarr",
        "hp": 42,
        "max_hp": 50,
        "hp_pct": 84.0,
        "status": "alive",
        "conditions": [],
    }
    yrsa = next(m for m in state["members"] if m["name"] == "Yrsa")
    assert yrsa["conditions"] == ["blessed"]


def test_party_from_dict_roundtrip():
    data = {
        "members": [
            {"name": "A", "hp": 5, "max_hp": 10, "status": "down",
             "conditions": ["stunned"]},
            {"name": "B", "hp": 10, "max_hp": 10},
        ]
    }
    party = party_from_dict(data)
    state = party.to_viewport_state()
    assert state["members"][0]["status"] == "down"
    assert state["members"][1]["status"] == "alive"
    member = member_from_dict({"name": "C", "hp": 3, "max_hp": 8})
    assert member.hp_pct == 37.5


# ---------------------------------------------------------------------------
# Slice 45 — divination adapter
# ---------------------------------------------------------------------------


def test_planet_positions_mock_flag_and_shape():
    result = divination.planet_positions(seed=1234)
    assert result["mock"] is True
    positions = result["positions"]
    assert set(positions) == set(divination.PLANETS)
    for degrees in positions.values():
        assert 0.0 <= degrees < 360.0


def test_planet_positions_deterministic():
    assert (
        divination.planet_positions(seed=7)["positions"]
        == divination.planet_positions(seed=7)["positions"]
    )
    assert (
        divination.planet_positions(seed=7)["positions"]
        != divination.planet_positions(seed=8)["positions"]
    )


def test_draw_tarot_spread():
    spread = divination.draw_tarot(3, seed=42)
    assert spread["mock"] is True
    assert [card["position"] for card in spread["spread"]] == [
        "Past",
        "Present",
        "Future",
    ]
    cards = [card["card"] for card in spread["spread"]]
    assert len(set(cards)) == 3  # no duplicates
    for card in spread["spread"]:
        assert isinstance(card["upright"], bool)


def test_draw_tarot_deterministic_and_full_deck():
    assert divination.draw_tarot(5, seed=1) == divination.draw_tarot(5, seed=1)
    full = divination.draw_tarot(78, seed=9)["spread"]
    assert len({card["card"] for card in full}) == 78
    with pytest.raises(ValueError):
        divination.draw_tarot(79)
    with pytest.raises(ValueError):
        divination.draw_tarot(0)


def test_draw_runes_cast():
    cast = divination.draw_runes(3, seed=42)
    assert cast["mock"] is True
    assert len(cast["cast"]) == 3
    runes = [entry["rune"] for entry in cast["cast"]]
    assert len(set(runes)) == 3
    assert set(runes) <= set(divination.ELDER_FUTHARK)
    for entry in cast["cast"]:
        assert isinstance(entry["upright"], bool)
    assert divination.draw_runes(3, seed=42) == divination.draw_runes(3, seed=42)
    with pytest.raises(ValueError):
        divination.draw_runes(25)


# ---------------------------------------------------------------------------
# Slice 46 — tool bridge
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    reg.register("add", lambda args: args["a"] + args["b"], description="Add")
    reg.register("greet", lambda args: f"hail, {args.get('name', 'stranger')}")
    yield reg
    reg.close()


def test_registry_invoke_success_envelope(registry):
    envelope = registry.invoke("add", {"a": 2, "b": 3})
    assert envelope == {
        "ok": True,
        "tool": "add",
        "args": {"a": 2, "b": 3},
        "result": 5,
    }


def test_registry_invoke_via_mcp_client_path(registry):
    # The same tools are visible through the MCP client round-trip.
    tools = registry.list_tools()
    assert {t["name"] for t in tools} == {"add", "greet"}
    envelope = registry.invoke("greet", {"name": "Volmarr"})
    assert envelope["ok"] is True
    assert envelope["result"] == "hail, Volmarr"


def test_registry_unknown_tool_envelope(registry):
    envelope = registry.invoke("missing", {})
    assert envelope["ok"] is False
    assert envelope["error_type"] == "unknown_tool"


def test_registry_tool_exception_becomes_error_envelope(registry):
    registry.register("boom", lambda args: 1 / 0)
    envelope = registry.invoke("boom", {})
    assert envelope["ok"] is False
    assert envelope["error_type"] == "tool_error"
    assert "ZeroDivisionError" in envelope["error"]


def test_registry_register_rules(registry):
    with pytest.raises(ToolInvocationError):
        registry.register("add", lambda args: None)  # duplicate
    with pytest.raises(ToolInvocationError):
        registry.register("", lambda args: None)  # empty name
    assert set(registry.tool_names()) == {"add", "greet"}
    registry.unregister("greet")
    assert registry.tool_names() == ["add"]
    with pytest.raises(ToolInvocationError):
        registry.unregister("greet")


def test_registry_invoke_after_close():
    reg = ToolRegistry()
    reg.register("ping", lambda args: "pong")
    reg.close()
    envelope = reg.invoke("ping", {})
    assert envelope["ok"] is False
    reg.close()  # idempotent

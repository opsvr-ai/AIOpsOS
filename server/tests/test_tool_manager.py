from src.services.tool_manager import _SkillTool, _build_schema, ToolManager


class TestBuildSchema:
    def test_empty_params(self):
        schema = _build_schema({})
        assert schema.__name__ == "_EmptyArgs"
        assert schema.model_json_schema()["properties"] == {}

    def test_typed_params(self):
        schema = _build_schema({"host": "str", "port": "int", "enabled": "bool", "ratio": "float"})
        props = schema.model_json_schema()["properties"]
        assert props["host"]["type"] == "string"
        assert props["port"]["type"] == "integer"
        assert props["enabled"]["type"] == "boolean"
        assert props["ratio"]["type"] == "number"

    def test_unknown_type_defaults_to_str(self):
        schema = _build_schema({"data": "json"})
        props = schema.model_json_schema()["properties"]
        assert props["data"]["type"] == "string"


class TestSkillTool:
    def test_basic_run(self):
        tool = _SkillTool(name="echo", description="Echo tool", args_schema=_build_schema({}))
        result = tool.run({})
        assert "echo" in result

    def test_run_with_args(self):
        schema = _build_schema({"msg": "str"})
        tool = _SkillTool(name="greet", description="Greeting", args_schema=schema)
        result = tool.run({"msg": "hello"})
        assert "greet" in result
        assert "hello" in result

    def test_async_run(self):
        import asyncio
        tool = _SkillTool(name="ping", description="Ping", args_schema=_build_schema({}))
        result = asyncio.run(tool.arun({}))
        assert "ping" in result


class TestToolManagerInit:
    def test_empty_on_startup(self):
        tm = ToolManager()
        assert tm.list_names() == []
        assert tm.get_tools() == []

    def test_get_tool_missing(self):
        tm = ToolManager()
        assert tm.get_tool("nonexistent") is None

    def test_get_tools_filtered(self):
        tm = ToolManager()
        assert tm.get_tools(names=["a", "b"]) == []

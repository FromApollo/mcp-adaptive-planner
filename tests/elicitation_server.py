"""Local MCP fixture used to exercise real server-to-client input requests."""
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

mcp = FastMCP("test_elicitation")


class Answer(BaseModel):
    value: str = Field(min_length=1)


@mcp.tool()
async def request_value(ctx: Context) -> str:
    result = await ctx.elicit(message="Укажите значение", schema=Answer)
    if result.action != "accept":
        raise ValueError("Input declined")
    return result.data.value


if __name__ == "__main__":
    mcp.run()

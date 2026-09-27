import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

_client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/"),
)
_model = os.getenv("LLM_MODEL", "gemini-3.6-flash")

_SYSTEM_TEMPLATE = """\
You are a task planning assistant. Given a user task, decompose it into steps \
that form a Directed Acyclic Graph (DAG) using only the available tools.

{tools_description}

Return ONLY a valid JSON object with this exact structure:
{{
  "steps": [
    {{
      "id": "1",
      "server": "server_name",
      "tool": "tool_name",
      "params": {{"param": "value"}},
      "depends_on": [],
      "description": "short description of what this step does"
    }}
  ]
}}

Rules:
- "depends_on" is a list of step IDs that must complete before this step runs.
- Steps with no dependencies (empty "depends_on") run in PARALLEL at the start.
- Use {{{{step_N.result}}}} in params to reference the output of step N.
- Steps that need {{{{step_N.result}}}} MUST include N in their "depends_on".
- Only use tools listed above. If the task requires a tool that is not available, \
return a single step with server "error" and tool "unavailable", explaining what is \
missing in the description.
- Keep steps minimal and direct.
- Maximise parallelism: if two steps are independent, give both empty "depends_on".

Example — "get CPU and memory info, then write a summary file":
{{
  "steps": [
    {{"id":"1","server":"sysinfo","tool":"get_cpu","params":{{}},"depends_on":[],"description":"Get CPU usage"}},
    {{"id":"2","server":"sysinfo","tool":"get_memory","params":{{}},"depends_on":[],"description":"Get memory usage"}},
    {{"id":"3","server":"filesystem","tool":"write_file","params":{{"path":"data/summary.txt","content":"CPU:\\n{{{{step_1.result}}}}\\nRAM:\\n{{{{step_2.result}}}}"}},"depends_on":["1","2"],"description":"Write combined summary"}}
  ]
}}
"""


def plan(task: str, tools_description: str) -> dict:
    import time
    from openai import RateLimitError

    system_prompt = _SYSTEM_TEMPLATE.format(tools_description=tools_description)
    last_exc: Exception | None = None

    for attempt in range(3):
        try:
            response = _client.chat.completions.create(
                model=_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Task: {task}"},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            raw = response.choices[0].message.content
            data = json.loads(raw)
            _validate_dag(data)
            return data
        except RateLimitError as exc:
            last_exc = exc
            if attempt < 2:
                wait = 35 * (attempt + 1)   # 35 s, 70 s
                time.sleep(wait)
        except Exception:
            raise

    raise last_exc  # type: ignore[misc]


def _validate_dag(plan: dict) -> None:
    steps = plan.get("steps", [])
    ids = {s["id"] for s in steps}

    for step in steps:
        for dep in step.get("depends_on", []):
            if dep not in ids:
                raise ValueError(
                    f"Step '{step['id']}' depends on unknown step '{dep}'"
                )

    # Cycle detection via DFS
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {s["id"]: WHITE for s in steps}
    dep_map = {s["id"]: s.get("depends_on", []) for s in steps}

    def dfs(node: str) -> None:
        color[node] = GRAY
        for neighbour in dep_map[node]:
            if color[neighbour] == GRAY:
                raise ValueError(f"DAG cycle detected involving step '{node}'")
            if color[neighbour] == WHITE:
                dfs(neighbour)
        color[node] = BLACK

    for sid in ids:
        if color[sid] == WHITE:
            dfs(sid)

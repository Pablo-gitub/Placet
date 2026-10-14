import os
import re
from pathlib import Path
from typing import Dict, List, Optional
import yaml
from pydantic import BaseModel, Field


class AgentSpec(BaseModel):
    name: str = ""
    tmux_session: str
    # Secret: never serialized (e.g. by GET /api/agents).
    token: str = Field("", exclude=True)
    allowed_targets: List[str] = Field(default_factory=list)
    default_timeout_seconds: int = 180
    # Regexes recognising the empty input prompt of the agent's TUI, checked
    # on the last lines of the screen; empty means "plain shell prompt".
    idle_patterns: List[str] = Field(default_factory=list)
    # Extra substrings meaning "busy" (besides the built-in interrupt hints).
    busy_patterns: List[str] = Field(default_factory=list)


class RouteRule(BaseModel):
    require_approval: bool = True


class PlacetConfig(BaseModel):
    agents: Dict[str, AgentSpec] = Field(default_factory=dict)
    rules: Dict[str, RouteRule] = Field(default_factory=dict)
    description_markdown: str = ""

    def get_agent(self, agent_id: str) -> Optional[AgentSpec]:
        return self.agents.get(agent_id)

    def requires_approval(self, source: str, target: str) -> bool:
        """
        Check if sending from source to target requires human approval.
        If target is 'human', approval is inherently required (or rather, human is the target).
        If rule is undefined, defaults to True for safety.
        """
        if target == "human":
            return True
        key = f"{source} -> {target}"
        if key in self.rules:
            return self.rules[key].require_approval
        return True

    def is_route_allowed(self, source: str, target: str) -> bool:
        """
        Check if source agent is authorized to send tasks to target.
        """
        agent = self.get_agent(source)
        if not agent:
            return False
        return target in agent.allowed_targets


def _interpolate_env_vars(text: str) -> str:
    """
    Replaces patterns like ${VAR:-default} or ${VAR} with environment variable values.
    """
    pattern = re.compile(r"\$\{([A-Za-z0-9_]+)(?::-([^}]*))?\}")

    def replacer(match):
        var_name = match.group(1)
        default_val = match.group(2) if match.group(2) is not None else ""
        return os.environ.get(var_name, default_val)

    return pattern.sub(replacer, text)


def load_agents_config(file_path: Optional[str] = None) -> PlacetConfig:
    if file_path is None:
        # Default to AGENTS.md in root of workspace
        file_path = str(Path(__file__).resolve().parent.parent.parent / "AGENTS.md")

    path = Path(file_path)
    if not path.exists():
        return PlacetConfig()

    content = path.read_text(encoding="utf-8")
    
    # Split frontmatter if present
    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
    if frontmatter_match:
        yaml_part = frontmatter_match.group(1)
        markdown_part = frontmatter_match.group(2)
    else:
        yaml_part = content
        markdown_part = ""

    interpolated_yaml = _interpolate_env_vars(yaml_part)
    data = yaml.safe_load(interpolated_yaml) or {}

    agents_data = data.get("agents", {})
    rules_data = data.get("rules", {})

    agents: Dict[str, AgentSpec] = {}
    for agent_id, a_data in agents_data.items():
        if isinstance(a_data, dict):
            name = a_data.get("name") or agent_id
            agents[agent_id] = AgentSpec(
                name=name,
                tmux_session=a_data.get("tmux_session", f"agent-{agent_id}"),
                token=a_data.get("token", ""),
                allowed_targets=a_data.get("allowed_targets", []),
                default_timeout_seconds=a_data.get("default_timeout_seconds", 180),
                idle_patterns=a_data.get("idle_patterns", []),
                busy_patterns=a_data.get("busy_patterns", []),
            )

    rules: Dict[str, RouteRule] = {}
    for rule_key, r_data in rules_data.items():
        if isinstance(r_data, dict):
            rules[rule_key] = RouteRule(
                require_approval=r_data.get("require_approval", True)
            )

    return PlacetConfig(
        agents=agents,
        rules=rules,
        description_markdown=markdown_part.strip(),
    )

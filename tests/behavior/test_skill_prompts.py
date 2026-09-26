from investorch.agents.prompts import MAIN_AGENT_INSTRUCTIONS, PERMISSION_AGENT_INSTRUCTIONS


def test_main_prompt_keeps_core_and_loads_specialized_knowledge_on_demand():
    for removed in ("memory/rqalpha.md", "Portfolio rules:", "RQAlpha strategy work:", "Bootstrap"):
        assert removed not in MAIN_AGENT_INSTRUCTIONS
    for required in ("load_skill", "Skill catalog", "Memory rules:", "approval", "economic facts"):
        assert required in MAIN_AGENT_INSTRUCTIONS


def test_permission_prompt_distinguishes_candidates_from_installed_behavior():
    for required in (
        ".skill-staging/",
        "installed Skill",
        "PASS does not",
        "WARN requires manual",
        "BLOCK must not",
        "built-in",
        "fork",
    ):
        assert required in PERMISSION_AGENT_INSTRUCTIONS

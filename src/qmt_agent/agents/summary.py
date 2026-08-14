from agents import Agent, OpenAIResponsesModel

from .prompts import SUMMARY_AGENT_INSTRUCTIONS


def create_summary_agent(model: OpenAIResponsesModel,) -> Agent:
    return Agent(
        name="Trace Summary Agent",
        instructions=SUMMARY_AGENT_INSTRUCTIONS,
        model=model,
    )
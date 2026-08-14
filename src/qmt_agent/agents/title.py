from agents import Agent, OpenAIResponsesModel

from .prompts import TITLE_AGENT_INSTRUCTIONS


def create_title_agent(model: OpenAIResponsesModel,) -> Agent:
    return Agent(
        name="Session Title Agent",
        instructions=TITLE_AGENT_INSTRUCTIONS,
        model=model,
    )
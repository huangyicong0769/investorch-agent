from agents import Agent, OpenAIResponsesModel

from .prompts import MAIN_AGENT_INSTRUCTIONS


def create_agent(model: OpenAIResponsesModel) -> Agent:
    return Agent(
        name="QMT Agent",
        instructions=MAIN_AGENT_INSTRUCTIONS,
        model=model,
    )


if __name__ == "__main__":
    import os

    from agents import Runner, set_tracing_disabled
    from dotenv import load_dotenv
    from openai import AsyncOpenAI

    load_dotenv()

    set_tracing_disabled(True)
    
    client = AsyncOpenAI(
        api_key = os.environ.get("DEEPSEEK_API_KEY"),
        base_url = os.environ.get("DEEPSEEK_BASE_URL"),
    )

    model = OpenAIResponsesModel(
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        openai_client = client,
    )

    agent = create_agent(model)

    user_input = input("You ")

    result = Runner.run_sync(
        agent,
        user_input,
    )

    print("Agent:", result.final_output)

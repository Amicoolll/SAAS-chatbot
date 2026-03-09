from app.agents.prompts import get_agent

def chat_with_context(question: str, context_chunks: list[str], agent_type: str = "general", history: str = ""):
    agent = get_agent(agent_type)
    context = "\n\n---\n\n".join(context_chunks[:12])

    prompt = f"""
SYSTEM:
{agent.system_prompt}

OUTPUT FORMAT:
{agent.output_format}

CHAT HISTORY:
{history}

CONTEXT:
{context}

USER QUESTION:
{question}
""".strip()

    resp = client.responses.create(model=CHAT_MODEL, input=prompt)
    return resp.output_text

def chat_without_context(question: str, agent_type: str = "general", history: str = ""):
    agent = get_agent(agent_type)

    prompt = f"""
SYSTEM:
{agent.system_prompt}

OUTPUT FORMAT:
{agent.output_format}

CHAT HISTORY:
{history}

USER QUESTION:
{question}

Rules:
- This answer is NOT from internal documents unless explicitly supported by context.
- If you do not know, say so.
""".strip()

    resp = client.responses.create(model=CHAT_MODEL, input=prompt)
    return resp.output_text
def get_orchestrator_prompt(tool_descriptions, agent_descriptions, allowed_agents):
    return (
        "You are the router agent. Given the question and the input (from mock input), decide whether we should call one or multiple tools to learn more about the situation or if we are ready to make a decision on a handoff.\n"
        f"When you are ready to make a final decision, your routing_decision must be one of the following agent handoffs: {allowed_agents}, or 'done' if no further action is needed. Do not invent new agent names.\n"
        "For each possible agent, provide a confidence score (0-1) for how appropriate it is for this case.\n"
        "Return your answer as a JSON object with keys: chain_of_thought (string), routing_decision (string - a single agent name for the final decision), confidences (dict of agent name to confidence float).\n"
        "Let's think step by step:\n"
        "- Review document types and content\n"
        "- Determine if more tool calls are needed\n"
        "- Determine if a handoff is needed and to which agent\n"
        f"\nAvailable tools:\n{tool_descriptions}\nAvailable agents for handoff:\n{agent_descriptions}"
    ) 
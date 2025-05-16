from openai import OpenAI
from langsmith import traceable
from langsmith.wrappers import wrap_openai
from os import getenv
from dotenv import load_dotenv
from time import sleep
import json
import os
# Import mock inputs for testing
from .tools_and_agents import TOOLS, AGENTS, get_tool_by_name
from prompts import get_orchestrator_prompt
from tool_functions import (
    search_latest_knowledge,
    VectorStoreRetriever,
    process_client_submissions,
    lookup_HSCode_details
)

load_dotenv()

openai_client = wrap_openai(OpenAI())

USE_MOCK = os.getenv("USE_MOCK", "false").lower() == "true"

@traceable(run_type="retriever")
def retriever(query: str):
    results = ["Harrison worked at Kensho"]
    return results

@traceable(name="ChatPromptTemplate", run_type="prompt")
def chat_prompt_template(system_message, question=None, tool_outputs=None):
    messages = [{"role": "system", "content": system_message}]
    if question:
        messages.append({"role": "user", "content": question})
    if tool_outputs:
        tool_context = "\nTool outputs:\n" + "\n".join([f"{tool}: {output}" for tool, output in tool_outputs.items()])
        messages.append({"role": "system", "content": tool_context})
    return messages

@traceable(run_type="llm")
def call_llm(messages):
    return openai_client.chat.completions.create(
        messages=messages,
        model="gpt-4o-mini",
    )

@traceable(name="RunnableLambda")
def parse_tool_output(tool_output):
    # Simulate parsing tool output
    sleep(0.1)
    return f"Parsed: {tool_output}"

@traceable(name="RunnableMap")
def decide_and_call_tool(question, tool_list):
    prompt = f"Given the question: '{question}', and these tools: {tool_list}, which tool(s) should be used? Respond with a comma-separated list of tool names, or a JSON list if you prefer."
    messages = chat_prompt_template(prompt)  # No need to pass question again
    llm_response = chat_openai(messages)
    # Try to parse as JSON list first
    if hasattr(llm_response, 'choices'):
        content = llm_response.choices[0].message.content.strip()
    elif isinstance(llm_response, dict) and 'choices' in llm_response:
        content = llm_response['choices'][0]['message']['content'].strip()
    else:
        content = str(llm_response).strip()
    try:
        # Try to parse as JSON list
        tool_decisions = json.loads(content)
        if isinstance(tool_decisions, list):
            return [t.strip() for t in tool_decisions]
    except Exception:
        # Fallback: split by comma or newline
        if ',' in content:
            return [t.strip() for t in content.split(',') if t.strip()]
        else:
            return [t.strip() for t in content.split('\n') if t.strip()]

@traceable(name="Chat", run_type="llm")
def chat_openai(messages):
    if USE_MOCK and MOCK_ROUTER_RESPONSE:
        class MockResponse:
            def __init__(self, content):
                self.choices = [type('obj', (object,), {'message': type('obj', (object,), {'content': content})})()]
        return MockResponse(MOCK_ROUTER_RESPONSE)
    return openai_client.chat.completions.create(
        messages=messages,
        model="gpt-4o-mini",
    )

def get_llm_content(llm_response):
    """Helper function to extract content from LLM response consistently."""
    if hasattr(llm_response, 'choices'):
        return llm_response.choices[0].message.content
    elif isinstance(llm_response, dict) and 'choices' in llm_response:
        return llm_response['choices'][0]['message']['content']
    return str(llm_response)

@traceable(name="AgentOutputParser", run_type="parser")
def agent_output_parser(llm_response):
    try:
        content = get_llm_content(llm_response)
        data = json.loads(content)
        return {
            "chain_of_thought": data.get("chain_of_thought", ""),
            "routing_decision": data.get("routing_decision", []),
            "confidences": data.get("confidences", {})
        }
    except Exception:
        return {
            "chain_of_thought": "Could not parse LLM output.",
            "routing_decision": [],
            "confidences": {}
        }

def execute_tool(tool_name, question):
    """Helper function to execute tools consistently."""
    if tool_name == "search_latest_knowledge":
        return search_latest_knowledge(question)
    elif tool_name == "vector_store_retriever":
        return VectorStoreRetriever(question)
    elif tool_name == "batch_process_client_docs":
        return process_client_submissions(["file1.pdf", "file2.pdf", "file3.pdf"])
    elif tool_name == "lookup_HSCode_details":
        return lookup_HSCode_details("0101.21")
    return None

@traceable(name="OrchestratorAgent")
def orchestrator_agent(question, tool_outputs=None):
    tool_descriptions = "\n".join([f"- {tool['name']}: {tool['description']}" for tool in TOOLS])
    agent_descriptions = "\n".join([f"- {agent['name']}: {agent['description']}" for agent in AGENTS])
    allowed_agents = ', '.join([agent['name'] for agent in AGENTS])
    base_prompt = get_orchestrator_prompt(tool_descriptions, agent_descriptions, allowed_agents)
    plan = decide_and_call_tool(question, [tool['name'] for tool in TOOLS])
    # Handle both single tool name and list of tool names
    tool_output = None
    if isinstance(plan, list):
        tool_output = {tool_name: execute_tool(tool_name, question) for tool_name in plan}
    elif isinstance(plan, str):
        tool_output = execute_tool(plan, question)
    parsed_plan = parse_tool_output(plan)
    system_message = base_prompt + f"\n\nQuestion: {question}"
    messages = chat_prompt_template(system_message, question, tool_outputs)  # Include tool outputs in context
    llm_response = chat_openai(messages)
    parsed_llm = agent_output_parser(llm_response)
    return {
        "plan": plan,
        "parsed_plan": parsed_plan,
        "tool_output": tool_output,
        "llm_response": llm_response,
        "parsed_llm": parsed_llm,
        "chain_of_thought": parsed_llm["chain_of_thought"],
        "routing_decision": parsed_llm["routing_decision"],
        "confidences": parsed_llm["confidences"]
    }

def format_case_dict(case_dict):
    return "\n".join(f"{k}: {v}" for k, v in case_dict.items())

@traceable(name="RunnableAgent")
def runnable_agent(case_dict):
    question = format_case_dict(case_dict)
    context = {"question": question, "tool_outputs": {}}
    trajectory = []
    max_steps = 10
    steps = 0
    allowed_agents = [a["name"].lower() for a in AGENTS]
    while steps < max_steps:
        orchestrator_result = orchestrator_agent(context["question"], context["tool_outputs"])
        trajectory.append({
            "orchestrator_result": orchestrator_result,
            "tool_outputs": dict(context["tool_outputs"]),
        })
        parsed_plan = orchestrator_result.get("parsed_plan", None)
        plan = orchestrator_result.get("plan", None)
        plan_tools = []
        if isinstance(plan, list):
            plan_tools = plan
        elif isinstance(plan, str):
            plan_tools = [plan]
        for tool_name in plan_tools:
            if tool_name and tool_name not in context["tool_outputs"] and tool_name not in allowed_agents and tool_name != "done":
                context["tool_outputs"][tool_name] = execute_tool(tool_name, question)
        routing_decision = orchestrator_result.get("routing_decision", [])
        normalized = [a.lower().strip() for a in routing_decision]
        if all(agent in allowed_agents for agent in normalized) or normalized == ["done"]:
            break
        # routing_decision can be a list of tool names
        for tool_name in routing_decision:
            if tool_name in context["tool_outputs"]:
                continue
            if isinstance(tool_name, list):
                for t in tool_name:
                    if t not in context["tool_outputs"]:
                        context["tool_outputs"][t] = execute_tool(t, question)
            else:
                context["tool_outputs"][tool_name] = execute_tool(tool_name, question)
        steps += 1
    return {
        "trajectory": trajectory,
        "final_decision": orchestrator_result
    }

if __name__ == "__main__":
    import tests.mock_inputs as case
    result = runnable_agent(case.MOCK_CASE_5)
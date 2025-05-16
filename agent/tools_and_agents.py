from tool_functions import (
    search_latest_knowledge,
    VectorStoreRetriever,
    process_client_submissions,
    lookup_HSCode_details
)

# Tool definitions (only actual tools)
TOOLS = [
    {
        "name": "search_latest_knowledge",
        "description": "Searches the latest knowledge base for relevant information.",
        "function": search_latest_knowledge,
    },
    {
        "name": "vector_store_retriever",
        "description": "Retrieves relevant information from the vector store based on the query.",
        "function": VectorStoreRetriever,
    },
    {
        "name": "batch_process_client_docs",
        "description": "Processes and extracts information from all client-uploaded PDF documents.",
        "function": process_client_submissions,
    },
    {
        "name": "lookup_HSCode_details",
        "description": "Looks up details and regulations for a given HSCode.",
        "function": lookup_HSCode_details,
    },
]

# Agent definitions (handoff targets, not tools)
AGENTS = [
    {
        "name": "declaration_review",
        "description": "Use when product classification is unclear, documentation is incomplete or ambiguous, or multiple HS/HTS codes apply.",
    },
    {
        "name": "regulatory_sustainability",
        "description": "Use when the item may violate import/export laws, require special licenses, or involve ESG, REACH, CBAM, or other regulatory compliance concerns.",
    },
    {
        "name": "sourcing_logistics",
        "description": "Use when everything is in order and the shipment is routine, well-documented, and ready for normal processing.",
    },
]

def get_tool_by_name(name):
    """Helper to get tool by name"""
    for tool in TOOLS:
        if tool["name"] == name:
            return tool
    return None 
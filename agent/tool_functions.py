from time import sleep
from langsmith import traceable

@traceable(run_type="tool")
def search_latest_knowledge(query: str):
    sleep(0.2)
    hscode_results_1 = lookup_HSCode_details("0101.21")
    hscode_results_2 = lookup_HSCode_details("0202.30")
    hscode_results_3 = lookup_HSCode_details("0303.40")
    return ["Latest knowledge about: " + query] + hscode_results_1 + hscode_results_2 + hscode_results_3

@traceable
def VectorStoreRetriever(query: str):
    sleep(0.15)
    return ["Vector store result for: " + query]

@traceable(run_type="tool")
def websearch_latest_knowledge(query: str):
    return ["Tariff impact for: " + query]

@traceable(name="search_HSCode_details", run_type="retriever")
def lookup_HSCode_details(code: str):
    sleep(0.3)
    return [f"HSCode search result for: {code}"]

@traceable(name="pdf_extract_text", run_type="retriever")
def extract_text_from_pdf(file: str):
    sleep(0.4)
    return f"Parsed content of {file}"

@traceable(name="batch_process_client_docs")
def process_client_submissions(files):
    sleep(0.1)
    parsed_docs = []
    for idx, file in enumerate(files):
        content = extract_text_from_pdf(file)
        doc = {
            "PageContent": content,
            "Metadata": {
                "Loc": {
                    "Lines": {
                        "From": 10 * idx + 1,
                        "To": 10 * idx + 5
                    }
                }
            }
        }
        parsed_docs.append(doc)
    return parsed_docs 
import os
import re

from dotenv import load_dotenv
from neo4j import GraphDatabase

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

NEO4J_URI = os.environ["NEO4J_URI"]
NEO4J_USERNAME = os.environ["NEO4J_USERNAME"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]
NEO4J_DATABASE = os.environ["NEO4J_DATABASE"]

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL") or None
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


# ============================================================
# GRAPH SCHEMA
# ============================================================

schema_path = os.path.join(
    os.path.dirname(__file__),
    "graph_schema_prompt.md"
)

with open(schema_path, encoding="utf-8") as f:
    GRAPH_SCHEMA_PROMPT = f.read()


# ============================================================
# NEO4J CONNECTION
# ============================================================

driver = GraphDatabase.driver(
    NEO4J_URI,
    auth=(NEO4J_USERNAME, NEO4J_PASSWORD)
)


def run_query(cypher: str, params: dict | None = None):

    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(cypher, params or {})
        return [record.data() for record in result]


# ============================================================
# TOOL 1
# SPECIFIC TICKET CONTEXT
# ============================================================

@tool
def get_ticket_context(ticket_key: str) -> str:
    """
    Get complete context about a specific ticket.

    Returns:
    - status
    - priority
    - assignee
    - blockers
    - issue
    - related decisions
    - historical tickets with the same issue
    - previous decisions for that issue
    """

    rows = run_query(
        """
        MATCH (t:Ticket {key: $key})

        OPTIONAL MATCH
            (assignee:Employee)-[:ASSIGNED_TO]->(t)

        OPTIONAL MATCH
            (blocker:Ticket)-[:BLOCKS]->(t)

        OPTIONAL MATCH
            (t)-[:CAUSED_BY]->(issue:Issue)

        OPTIONAL MATCH
            (t)-[:DISCUSSED_IN]->
            (:Message)-[:LED_TO]->
            (dec:Decision)

        RETURN
            t.key AS key,
            t.title AS title,
            t.status AS status,
            t.priority AS priority,
            assignee.name AS assignee,
            collect(DISTINCT blocker.key) AS blockers,
            issue.name AS issue,
            collect(DISTINCT dec.description) AS decisions
        """,
        {"key": ticket_key},
    )

    if not rows or rows[0]["key"] is None:
        return f"No ticket found with key {ticket_key}."

    r = rows[0]

    lines = [
        f"Ticket: {r['key']}",
        f"Title: {r['title']}",
        f"Status: {r['status']}",
        f"Priority: {r['priority']}",
    ]

    if r["assignee"]:
        lines.append(
            f"Assigned to: {r['assignee']}"
        )

    blockers = [
        b for b in r["blockers"]
        if b
    ]

    if blockers:
        lines.append(
            f"Blocked by: {', '.join(blockers)}"
        )

    issue = r["issue"]

    if issue:
        lines.append(
            f"Issue: {issue}"
        )

    decisions = [
        d for d in r["decisions"]
        if d
    ]

    for decision in decisions:
        lines.append(
            f"Decision: {decision}"
        )

    # --------------------------------------------------------
    # HISTORICAL MEMORY
    # --------------------------------------------------------

    if issue:

        history = run_query(
            """
            MATCH
                (i:Issue {name: $issue})
                <-[:CAUSED_BY]-
                (other:Ticket)

            WHERE other.key <> $key

            OPTIONAL MATCH
                (other)-[:DISCUSSED_IN]->
                (:Message)-[:LED_TO]->
                (histDec:Decision)

            OPTIONAL MATCH
                (histDec)-[:MADE_BY]->
                (person:Employee)

            RETURN
                other.key AS ticket,
                other.title AS title,
                other.status AS status,
                histDec.description AS decision,
                person.name AS decided_by
            """,
            {
                "issue": issue,
                "key": ticket_key,
            },
        )

        for h in history:

            if h["decision"]:

                lines.append(
                    "\nHISTORICAL MEMORY:"
                )

                lines.append(
                    f"Ticket {h['ticket']} previously had "
                    f"the same issue: {issue}."
                )

                lines.append(
                    f"Previous decision: \"{h['decision']}\""
                )

                if h["decided_by"]:
                    lines.append(
                        f"Decision made by: {h['decided_by']}"
                    )

    else:

        lines.append(
            "\nNo issue has been tagged for this ticket yet."
        )

        lines.append(
            "Therefore no historical issue memory is available."
        )

    return "\n".join(lines)


# ============================================================
# TOOL 2
# MULTI-HOP FEATURE BLOCKER TRACE
# ============================================================

@tool
def trace_feature_blockers(feature_name: str) -> str:
    """
    Explain why a feature is blocked.

    Only tickets whose status is actually "Blocked"
    are considered.

    Example:

        Checkout
           ↓
        MYN-123
           ↓ BLOCKED BY
        MYN-118
           ↓ BLOCKED BY
        MYN-45
           ↓ CAUSED_BY
        Authentication API Timeout
           ↓
        Decision
           ↓
        Employee
    """

    rows = run_query(
        """
        MATCH
            (f:Feature {name: $feature_name})
            -[:HAS_TICKET]->
            (target:Ticket)

        WHERE target.status = "Blocked"

        MATCH path =
            (root:Ticket)
            -[:BLOCKS*0..]->
            (target)

        WHERE NOT EXISTS {
            MATCH ()-[:BLOCKS]->(root)
        }

        WITH
            target,
            [node IN nodes(path) | node.key] AS chain,
            root

        OPTIONAL MATCH
            (root)-[:CAUSED_BY]->(issue:Issue)

        OPTIONAL MATCH
            (root)-[:DISCUSSED_IN]->
            (:Message)-[:LED_TO]->
            (decision:Decision)

        OPTIONAL MATCH
            (decision)-[:MADE_BY]->
            (person:Employee)

        RETURN
            target.key AS target_ticket,
            target.status AS target_status,
            chain,
            issue.name AS issue,
            decision.description AS decision,
            person.name AS decided_by
        """,
        {
            "feature_name": feature_name
        },
    )

    if not rows:
        return (
            f"No blocked tickets found for "
            f"feature '{feature_name}'."
        )

    output = [
        f"Feature: {feature_name}",
        "",
        "MULTI-HOP BLOCKER ANALYSIS:"
    ]

    for r in rows:

        chain = r["chain"]

        # Cypher returns:
        #
        # MYN-45 -> MYN-118 -> MYN-123
        #
        # Reverse it so the explanation starts
        # from the blocked ticket.

        reversed_chain = list(reversed(chain))

        output.append("")

        output.append(
            f"Target ticket: {r['target_ticket']} "
            f"({r['target_status']})"
        )

        output.append(
            "Blocker chain:"
        )

        for i, ticket in enumerate(reversed_chain):

            if i == 0:
                output.append(ticket)

            else:
                output.append(
                    f"  -> BLOCKED BY\n"
                    f"{ticket}"
                )

        if r["issue"]:
            output.append(
                f"\nRoot cause issue: {r['issue']}"
            )

        if r["decision"]:
            output.append(
                f"Decision: {r['decision']}"
            )

        if r["decided_by"]:
            output.append(
                f"Decision made by: {r['decided_by']}"
            )

    return "\n".join(output)


# ============================================================
# TOOL 3
# TEACH / WRITE TO GRAPH
# ============================================================

@tool
def tag_ticket_issue(
    ticket_key: str,
    issue_name: str
) -> str:
    """
    Tag a ticket with an existing issue.

    Example:

        tag MYN-90 as Authentication API Timeout

    Writes:

        Ticket -[:CAUSED_BY]-> Issue
    """

    rows = run_query(
        """
        MATCH
            (t:Ticket {key: $ticket_key}),
            (i:Issue {name: $issue_name})

        MERGE
            (t)-[:CAUSED_BY]->(i)

        RETURN
            t.key AS ticket,
            i.name AS issue
        """,
        {
            "ticket_key": ticket_key,
            "issue_name": issue_name,
        },
    )

    if not rows:

        return (
            f"Could not tag {ticket_key} with "
            f"'{issue_name}'. "
            f"Check that both exist in Neo4j."
        )

    return (
        f"Tagged {rows[0]['ticket']} with issue "
        f"'{rows[0]['issue']}'. "
        f"Project memory updated."
    )


# ============================================================
# TOOL 4
# LLM-GENERATED READ-ONLY CYPHER
# ============================================================

_WRITE_KEYWORDS = re.compile(
    r"\b("
    r"CREATE|"
    r"MERGE|"
    r"DELETE|"
    r"DETACH|"
    r"SET|"
    r"REMOVE|"
    r"DROP|"
    r"CALL\s+apoc\."
    r")\b",
    re.IGNORECASE,
)


@tool
def query_graph_freeform(cypher_query: str) -> str:
    """
    Execute an LLM-generated READ-ONLY Cypher query.

    Use this only when the other three specialized tools
    do not fit the user's question.

    Allowed:
        MATCH
        OPTIONAL MATCH
        WHERE
        WITH
        RETURN
        ORDER BY
        LIMIT

    Never perform writes.
    """

    if _WRITE_KEYWORDS.search(cypher_query):

        return (
            "REFUSED: This tool only allows read-only Cypher. "
            "The generated query contained a write operation. "
            "Use tag_ticket_issue for supported graph updates."
        )

    try:

        rows = run_query(cypher_query)

    except Exception as e:

        return (
            f"Cypher execution failed: {e}\n"
            "Check the graph schema and generate a corrected "
            "read-only query."
        )

    if not rows:
        return (
            "Query executed successfully, "
            "but returned no results."
        )

    preview = rows[:25]

    output = [
        str(row)
        for row in preview
    ]

    if len(rows) > 25:

        output.append(
            f"... {len(rows) - 25} additional rows not shown."
        )

    return "\n".join(output)


# ============================================================
# LLM
# ============================================================

llm = ChatOpenAI(
    model=OPENAI_MODEL,
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
    temperature=0,
)


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM_PROMPT = f"""
You are Project Memory, an AI engineering project assistant.

You have TWO kinds of memory.

1. CONVERSATION MEMORY

LangGraph remembers the current conversation.

Use this to resolve references such as:

- "that ticket"
- "the one we discussed"
- "what about it?"
- "what happened to that?"

2. PROJECT MEMORY

Neo4j stores the project's persistent knowledge graph:

- companies
- products
- teams
- employees
- features
- tickets
- issues
- messages
- decisions


============================================================
TOOLS
============================================================

Use tools in this priority order.

1. get_ticket_context

Use when the user gives a specific ticket key.

Example:

"What is happening with MYN-90?"


2. trace_feature_blockers

Use when the user asks why a feature is blocked.

Example:

"Why is Checkout blocked?"

This performs deterministic multi-hop graph traversal.

IMPORTANT:

Only tickets with status "Blocked" belong to the
blocking analysis.


3. tag_ticket_issue

Use when the user explicitly asks to tag/classify
a ticket.

Example:

"Tag MYN-90 as Authentication API Timeout."

This is the supported graph write operation.


4. query_graph_freeform

Use ONLY when the first three tools don't fit.

Examples:

"Which employees work on PhonePe?"

"List all high priority tickets."

"Who made the project decisions?"

For this tool you must generate READ-ONLY Cypher
using the graph schema below.

Never generate CREATE, MERGE, DELETE, SET, REMOVE,
DROP, or other write operations.


============================================================
IMPORTANT RULES
============================================================

- Do not invent project information.
- Use tools whenever project information is required.
- Treat Neo4j tool results as the source of truth.
- Historical memory is evidence from previous project activity.
- Do not automatically assume a historical solution is correct.
- Keep answers concise.
- If a user refers to an earlier message, use conversation memory.
- Never claim a graph relationship that was not returned by a tool.

============================================================
GRAPH SCHEMA
============================================================

{GRAPH_SCHEMA_PROMPT}
"""


# ============================================================
# LANGGRAPH MEMORY
# ============================================================

checkpointer = MemorySaver()


agent = create_react_agent(
    llm,
    tools=[
        get_ticket_context,
        trace_feature_blockers,
        tag_ticket_issue,
        query_graph_freeform,
    ],
    prompt=SYSTEM_PROMPT,
    checkpointer=checkpointer,
)


# ============================================================
# CONVERSATION THREAD
# ============================================================

CONFIG = {
    "configurable": {
        "thread_id": "demo-session-1"
    }
}


# ============================================================
# ASK + SHOW GRAPH EVIDENCE
# ============================================================

def ask_verbose(question: str) -> str:

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": question
                }
            ]
        },
        config=CONFIG,
    )

    # Show actual tool output during demo.
    # This lets the judge see what Neo4j returned
    # before the LLM summarizes it.

    for msg in result["messages"]:

        if msg.__class__.__name__ == "ToolMessage":

            print(
                f"\n[Graph evidence — {msg.name}]\n"
                f"{msg.content}\n"
            )

    return result["messages"][-1].content


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":

    print(
        "\nProject Memory Agent"
        "\nType 'exit' to quit.\n"
    )

    while True:

        question = input("> ").strip()

        if question.lower() in {
            "exit",
            "quit"
        }:
            break

        if not question:
            continue

        try:

            answer = ask_verbose(question)

            print(
                f"\nAgent: {answer}\n"
            )

        except Exception as e:

            print(
                f"\nError: {e}\n"
            )


    driver.close()
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Import your existing agent
from agent import agent, CONFIG


app = FastAPI(title="Project Memory")


class ChatRequest(BaseModel):
    message: str


@app.get("/")
def home():
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "index.html")
    )


@app.post("/chat")
def chat(request: ChatRequest):
    try:
        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": request.message
                    }
                ]
            },
            config=CONFIG,
        )

        evidence = []

        for msg in result["messages"]:
            if msg.__class__.__name__ == "ToolMessage":
                evidence.append({
                    "tool": getattr(msg, "name", "tool"),
                    "content": str(msg.content)
                })

        return {
            "answer": result["messages"][-1].content,
            "evidence": evidence,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

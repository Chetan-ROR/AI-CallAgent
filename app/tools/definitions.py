OPENAI_TOOLS = [
    {
        "type": "function",
        "name": "end_call",
        "description": (
            "End the current phone call after you have already said a short goodbye. "
            "The system waits until that goodbye audio finishes, then hangs up."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

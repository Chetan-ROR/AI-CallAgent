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
    {
        "type": "function",
        "name": "book_guest_pass",
        "description": (
            "Book the 7 Day Guest Pass only after the caller has clearly agreed. "
            "Do not call it during the greeting, on their first words, or in the same turn as the offer. "
            "First ask if they want the 7 Day Guest Pass, wait for a clear yes, then call this. "
            "If they are not already a member, pass the first name, last name, and email they just gave. "
            "If the tool asks for an email because they are not linked to Mindbody, ask for it and call again. "
            "If the tool says they already have the 7 Day Guest Pass, tell them that in one sentence and do not call this tool again. "
            "An expired pass can be booked again. Do not book the same plan while it is still active. "
            "Do not call this for any other plan. Do not collect a card or payment."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "first_name": {"type": "string", "description": "Required for a new caller."},
                "last_name": {"type": "string", "description": "Required for a new caller."},
                "email": {"type": "string", "description": "Required for a new caller."},
            },
            "required": [],
        },
    },
]

# Read-only workstation agent

Add a read-only agent to this application's Python backend using the installed
OpenAI Agents SDK. Reuse the existing rule lookup and Decision API functions.
Expose them as function tools and register them with the agent. Choose names
that fit this application; do not duplicate the underlying application logic.

Use these tool descriptions:
Rule lookup: [describe when the assistant should read and explain the rules].
Chart assessment: [describe when the assistant should request a new decision].

Capture the chart in the browser and bind that snapshot to the backend request.
Keep API credentials on the backend.
Return the actual tool results, including errors. Check that the tool names,
descriptions, and input schemas match the intended behavior. Verify the tool
connections before adding the chat interface. Report what passed and what
still needs a real API call. Follow CHECK, TAKE ACTION, and VERIFY if any
additional dependency change is needed.

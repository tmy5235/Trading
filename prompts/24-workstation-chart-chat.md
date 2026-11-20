# Workstation chart chat

Add a chat panel to this workstation and connect it to the backend SDK agent.
Let the user ask about the decision rules or request an assessment of the
chart currently on screen. Capture the chart when the message is sent.

Add this instruction to the assistant:
[write how it should handle an unclear request such as "Should I wait?"].

Show the assistant's response and an expandable record of the tools called
and their returned results. Report the ticker, chart time, choice, and returned
probability when available. Keep the chat assessment consistent with the app’s
chart result. If the app shows a probability, captured-image preview, or decision
marker, update those from the same tool result. A delayed response
for a different ticker or snapshot must not appear on the current chart.
Rule explanations must not trigger a chart assessment by themselves.

Verify a rule question and a chart assessment in the running app. Compare the
chat response, tool output, and chart display. Check a tool failure as well.
Keep the API key on the backend and leave trading actions outside this task.
Follow CHECK, TAKE ACTION, and VERIFY for any dependency change.

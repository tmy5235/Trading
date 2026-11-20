# Compare chart assessments in one conversation

Extend the chart assistant so it can compare a new assessment with an earlier
assessment of the same ticker and data mode in the same conversation. Keep
the previous tool result and chart time available to the agent. If there is
no earlier assessment, say so. Add a New chat control that starts a separate
conversation.

Test these questions through the running interface:
1. "What does WAIT mean in this app?"
2. "Run the decision on the chart I'm looking at. What do you recommend?"
3. After displaying a later chart snapshot in this app: "Run a new decision
   for this chart and compare it with the earlier assessment."

Use the same conversation for all three. Verify which tools ran, whether the
reported choices and probabilities match their results, and whether the
comparison identifies both chart times. Confirm that the chart updates for
the new assessment. Save screenshots and record the observed behavior,
including any mismatch. Follow CHECK, TAKE ACTION, and VERIFY if another
dependency change is needed.

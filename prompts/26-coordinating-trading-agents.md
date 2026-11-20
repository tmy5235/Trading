
Skip To Content
Dashboard
Trevor Michael Yuhaniak
Account
Dashboard
Courses
Groups
Calendar
Inbox
History
Kaltura
My Media
Notices
10
Help
IST 402
Pages
[In-class Activity 5] Coordinating Trading…


2268 - 202627FA
Home
Syllabus
Modules
Assignments
Quizzes
People
Announcements
Discussions
Files
Grades2
Notebook
[In-class Activity 5] Coordinating Trading Agents
↩ Module VII — Assignment 4: Live Market Workstation

Learning Objectives
Activity 4 added a chatbot that explains decision rules, assesses the displayed chart, and compares assessments. This activity extends that design to a watchlist. A Scanner Agent selects charts for review, and a Decision Agent uses the existing Decision API to assess each selected chart.

The activity continues with the OpenAI Agents SDK. Each agent has its own instructions and tools. Application code coordinates their runs and passes the scanner’s results to the Decision Agent.

By the end of the activity, students should be able to:

Define separate responsibilities and prompts for two agents.
Explain how the application passes evidence from one agent to another.
Verify a coordinated run using the chart evidence, tool results, and activity log.
Prerequisites
Start with the chatbot, rule lookup, and chart assessment from Activity 4. Use the same Python environment, Agents SDK installation, and backend .env configuration.

The instructor’s BabyBot demo uses five ticker rows at the same replay time. AAPL comes from a saved market recording. MSFT and NVDA use illustrative prices. AMD shows stale data, and SPY shows a missing capture. Each chart includes only candles available by its replay time. Students can use their own recorded watchlist or clearly labeled test data.

First build a read-only workflow. A paper-trading extension at the end adds positions and asks students to define HOLD and EXIT.

Build the Watchlist Workflow
Give each prompt to the coding agent in order. Replace the bracketed phrases with instructions for the workstation being built. Ask the coding agent to use the project’s CHECK, TAKE ACTION, and VERIFY process if a dependency change is needed.

Prepare the evidence
Inspect my Activity 4 chart assistant and its chart assessment tool.
Prepare a repeatable scan of [four or five tickers] at one recorded cutoff.
For each ticker, save the chart image and the two one-minute bars immediately
before that cutoff. Give each capture an ID. Show the bar times and
flag stale or missing evidence. Verify that advancing the replay does not
change a saved capture. Label any synthetic test data.
If setup needs a new dependency, CHECK the installed versions, TAKE ACTION
after approval for the exact change, and VERIFY the import in this project.
Create the two agents
Use the installed OpenAI Agents SDK. Give a Scanner Agent a tool that reads
the saved watchlist observations. Select charts when [my scan condition].
Rank ties by [my ranking rule]. Return up to [my limit] candidates, with
ticker, capture ID, and a short observation. Allow an empty list.

Have the backend check each selection and supply its original snapshot to a
Decision Agent. Reuse the rule reader, chart assessment tool, and decision
rules from my chart assistant. My Decision Agent should use its tools in [the order
I specify]. The chart panel should display [the result fields I choose]. Display each completed tool result beside its own chart,
including its chart time, returned choice, and probability when available.
Test a selected chart that receives WAIT and a scan with no candidates.
Add the run controls
Add Run once, Start, and Stop. Use [my interval], [my maximum cycles],
and [my session call limit]. Include calls to the Decision API in the limit.
Run one cycle at a time. Stop starting work when a limit is reached or the
user presses Stop. Record late, failed, and duplicate results separately.
Keep one completed assessment per candidate per cycle. Test a slow response
and repeated delivery. Show the scanner evidence and corresponding tool
result in a read-only activity log.
Demonstration

Open the demonstration video in Penn State SharePointLinks to an external site.

The instructor ran the same five-ticker replay twice. Both runs use the two completed one-minute candles before 14:42 UTC on November 16, 2026. Only the scanner threshold changes.

Selecting a Chart
AAPL qualifies after a $0.35 rise. Its red and green candles return WAIT (0.46) for 14:42 UTC.123
1. Scan condition
Any rise qualifies a ready ticker for review. 2. Selected chart
AAPL’s close rose by $0.35. MSFT fell, NVDA stayed flat, and the other two rows lack current evidence. 3. Decision for 14:42
The 14:40 candle closed down and the 14:41 candle closed up. The Decision API returned WAIT (0.46).
Open full-size screenshot
Before reading the chart result, compare AAPL’s two closes. The scanner asks whether the close rose from one minute to the next. The Decision API asks where each candle closed relative to its own open. AAPL can meet the scanner condition and still receive WAIT.

Following the Tool Calls
The log follows one AAPL capture from the scanner through rule lookup and chart assessment.123
1. Scanner
The first tool call reads the five observations. The next row identifies AAPL and quotes the two closes. 2. Capture ID
The coordinator retrieves AAPL-20261116T1442Z. The same ID appears on the Decision Agent’s calls. 3. Decision tools
The agent reads the shared rules, then calls assess_chart with the saved image. Row 09 records completion.
Open full-size screenshot
Follow the same capture ID from the scanner’s selection to the recorded assessment. The app shows the choice and the probability returned on that call. Running the model again can produce a different result.

Raising the Threshold
At the $0.50 threshold AAPL is skipped and the cycle ends without a Decision API call.123
1. Revised condition
Keep the same cutoff and require a rise of more than $0.50. 2. Chart preview
AAPL’s saved chart is still available to inspect. The panel has no assessment for this run. 3. End of cycle
The scanner completed its work. The last log entry says that no candidate met the condition.
Open full-size screenshot
Here the scanner still ran. With the minimum rise set to more than $0.50, it returned an empty list and the cycle ended. In the first run, WAIT was an answer from the Decision API. In this run there was no Decision API request.

Under the Hood
The coding agent builds the workflow from the three prompts. Use the diagram and excerpts below to review its implementation and locate the prompts and rules that control your own workstation.

Agent Workflow
Follow AAPL through both runs. The scanner receives the same closing prices each time. Changing its threshold determines whether the coordinator calls the Decision Agent. Agents SDK execution guideLinks to an external site.

Replay clock · 14:42 UTC
Freeze the watchlist rows and chart images together. A capture ID identifies one ticker at this cutoff.
↓
Scanner Agent · Which charts qualify?
Tool   read_watchlist supplies the completed closes, capture IDs, and data status.
Student’s condition   Select a chart when the second close exceeds the first by more than the chosen amount.
AAPL evidence   $354.65 → $355.00, a rise of $0.35.
↓
What did the scanner return?
A candidate · any rise
The coordinator checks the AAPL capture ID and retrieves that image.

Decision Agent · What does this chart support?
read_decision_rules supplies the shared conditions. assess_chart sends the image and question to the Decision API.

Student’s decision rule
Compare the two candle bodies. A red body followed by a green one calls for WAIT for 14:42.
 
Empty list · rise over $0.50
AAPL rose by only $0.35. No other ready ticker qualifies.

Coordinator
Record an empty scan and end the cycle here. There is no chart to send for assessment.
Activity log
A completed assessment has a capture ID, a choice, and a returned probability when available. An empty scan records the scanner’s work and zero assessments.
This one-candidate example uses two SDK runs. The coordinator reads the scanner’s shortlist and starts one Decision Agent run for each accepted candidate. A candidate includes the ticker, an observation, and the capture ID the backend uses to recover the image. This lets students trace a recommendation even if someone has selected a different chart in the interface.

SDK Orchestration
Activity 4 used one SDK Agent with two tools. This design uses two Agent definitions and invokes them in sequence through Runner.run. The coordinator validates the scanner’s structured output before passing candidates to the Decision Agent. Each run receives the context needed for its task.

Agents SDK components in the coordinated workflow
SDK component	Role in this activity	Student contribution
Agent.instructions	Defines each agent’s task and how it should use its tools.	Write separate scanner and decision prompts.
Agent.tools	Makes selected app functions available to each agent.	Describe what each tool does and when it should be called.
Agent.output_type	Defines the fields expected in the final agent result.	Specify the information the next stage needs.
Runner.run	Runs an agent’s model and tool exchanges until a result or stopping condition is reached.	Specify limits and how failures should be handled.
The SDK also supports a manager calling agents as tools and handoffs that transfer conversation control. The sequence in this lesson is controlled by the application, which always scans before assessing selected charts. Agents SDK orchestration guideLinks to an external site.

Application Coordinator
Run once takes one pass through the watchlist at the selected replay time. Start repeats the scan at successive recorded minutes through 14:44 UTC. Stop lets a request already in flight finish and prevents a new assessment from being saved for the stopped run. The monitor settings control the delay between cycles and the session’s model-call limit. Each call to the Decision API counts toward that limit along with the agents’ model requests.

Scanner Agent
Write the selection condition in SCANNER_PROMPT. In BabyBot, the question is whether the second completed close exceeds the first by more than the dollar amount selected on screen. A ready row includes the ticker, two completed bars, and the ID of its saved chart. AMD and SPY also appear in the watchlist so students can see why they were skipped.

The decorator exposes read_watchlist as an SDK tool. Its docstring tells the model what the tool returns. The function sends the frozen rows and the selected threshold to the model. Each ready row includes the signed dollar change and both closing prices formatted for the scanner’s explanation. The comparison uses the full precision of the saved prices. The coordinator creates this tool inside the scan function, so it can read that cycle’s rows. The flag evidence_read records whether the model actually called it.

@function_tool
async def read_watchlist() -> str:
    """Read this cycle's saved watchlist bars and capture IDs."""
    nonlocal evidence_read
    session.check_stop()
    evidence_read = True
    event(cycle, "Scanner", "read_watchlist returned the frozen watchlist observations.")
    return json.dumps(scanner_evidence(cycle["rows"], session.settings))
The model returns a list. Each entry names the ticker, identifies its saved chart, and explains the selection. The SDK uses the following types to read the response.

class Candidate(BaseModel):
    ticker: str
    snapshot_id: str
    observation: str

class Shortlist(BaseModel):
    candidates: list[Candidate]
For the first scan, the AAPL entry identifies the chart ending at 14:42 UTC. Its observation quotes the rise from $354.65 to $355.00. For the second scan, the list is empty. Both responses fit the same format.

Where to change the scan. Adjust the minimum rise in the interface to try the same rule with a different number. To try a different pattern, revise the scanner prompt and the check the backend uses to accept its shortlist. If the new condition needs volume or another field, add that evidence to the rows the tool returns.

Decision Agent
Write the tool instructions in DECISION_PROMPT. They tell the agent to read the current rules and call the chart assessment tool. To change the fields displayed after that call, ask the coding agent to revise the chart panel. The rules themselves come from Activity 3. Editing the shared question changes the meaning of LONG, SHORT, and WAIT for the chart button, chat assistant, and watchlist. Activity 3 decision choices

Passing the selected chart
The scanner returns a capture ID. The coordinator looks up the original chart and verifies that its ticker and replay time match. The Decision Agent gets a separate set of tool handlers for this candidate. In this implementation those handlers keep the snapshot in Python. The model can call the tools without supplying a ticker, changing the replay time, or attaching a new image.

# In the coordinator, after validating the shortlist
sid = candidate.snapshot_id
snapshot = captures.get(sid)
# Check the ticker and cutoff before running the Decision Agent.
await assess_candidate(session, cycle, snapshot, sid, model, options, config)
Inside assess_candidate, these calls connect the new agent to the same functions the class saw in Activity 4. The excerpt shows the shared handlers. BabyBot also checks Stop and counts requests around these calls. Activity 4 chart assessment tool

handlers = chat.ChartTools(snapshot)

@function_tool
def read_decision_rules() -> str:
    """Read the rules shared with the chart assistant."""
    return handlers.read_decision_rules()

@function_tool
async def assess_chart() -> str:
    """Assess this candidate's saved image."""
    return await handlers.assess_chart()
Running the agents
These two SDK definitions connect each prompt to the functions that agent may call. The scanner must return a shortlist. The Decision Agent finishes when its assessment tool returns. The chart panel reads the returned choice and its probability from that tool result.

scanner = Agent(name="Scanner", instructions=SCANNER_PROMPT, model=model,
                tools=[read_watchlist], output_type=Shortlist,
                model_settings=options)

agent = Agent(name="Decision", instructions=DECISION_PROMPT,
              model=model, model_settings=options,
              tools=[read_decision_rules, assess_chart],
              tool_use_behavior={"stop_at_tool_names": ["assess_chart"]})
Here is the order of the SDK calls for one candidate. The coordinator validates the shortlist and chart before starting the second run. Each accepted candidate gets its own Decision Agent run.

# Scanner
scan = await Runner.run(scanner, prompt, max_turns=4, run_config=config)
candidates = checked_candidates(scan.final_output, cycle["rows"], settings)

# For each accepted candidate
sid = candidate.snapshot_id
snapshot = captures.get(sid)
# The coordinator checks this snapshot and binds its tools to it.
# Inside assess_candidate, the Decision Agent runs on those tools.
await Runner.run(agent, "Read the shared rules and assess this saved chart.",
                 max_turns=5, run_config=config)
Result Validation
Before the first assessment, the backend checks the returned tickers and capture IDs against the ready rows, the scanner condition, and the candidate limit. An empty shortlist ends the cycle. After an assessment, the coordinator checks the ticker, replay time, candle time, target minute, and returned choice. It looks up the selected probability by the choice value. A probability of zero is a valid result.

result = handlers.assessment
# After the coordinator validates the saved tool result
probability = (result["probabilities"] or {}).get(result["choice"])
Activity Log
Use the log to answer three questions about a run. Which rows did the scanner read? Which saved chart did the coordinator retrieve? Which chart ID appears on the completed tool call? In the first scan, follow the AAPL capture ID through the log, then find it below the chart. Read the choice and probability in that chart’s assessment panel. In the second scan, the log ends after the scanner returns an empty list. A failed tool call appears as an error and has no completed assessment.

Student Exercises
Scanner Prompt
Complete the following instruction in your own words. Predict which recorded charts should qualify before running the scanner.

Select charts when [define the visible pattern]. Use [specify the required evidence]. Return at most [number] candidates, ranked by [criterion]. Report missing evidence as [expected behavior].
Compare the returned candidates with the predictions. Revise the prompt using one example the scanner included or excluded incorrectly.

Decision Prompt
Choose one condition for LONG, SHORT, or WAIT to revise. Give the coding agent both the revised choice description and an example chart. Ask the Decision Agent to read the updated rules before assessing that chart. Compare the returned label with your own reading of the two candle bodies. Record the probability shown beside the chart.

Define LONG as [condition], SHORT as [condition], and WAIT as [remaining cases]. Have the chart panel display [the evidence and result fields to inspect].
Run Controls
Specify how monitoring starts, how often it runs, and when it stops. Test the behavior with a slow tool response and a small cycle limit.

Start a new cycle when [trigger]. Stop after [limit]. If a cycle is still running, [behavior]. When the user selects Stop, [behavior for queued work and late results].
Paper Trading Extension
Students can extend the workflow with simulated positions and actions. This requires a position record, action tools, and explicit conditions for HOLD and EXIT. Define these functions before exposing them to an agent.

Example choices for a student paper-trading extension
Choice	Behavior to define
LONG	Open a simulated long position when the entry conditions and position state permit it.
SHORT	Open a simulated short position when the entry conditions and position state permit it.
HOLD	Retain an identified open position and record the assessment.
EXIT	Close the identified simulated position.
WAIT	Record the assessment without changing a position.
Write the conditions for each choice. Include the position information the decision requires, the permitted actions, and the probability fields to display. The application must check the current position before applying an action and record each action once. For example, a decision based on position version 7 must be rechecked if the position has changed to version 8. If an action times out, check its recorded outcome before retrying.

Verification
Checks for the coordinated agent workflow
Case	Expected observation
No candidate	The scanner returns an empty list and the cycle completes with no chart assessments.
Scanner selects AAPL, Decision API returns WAIT	Both results are recorded with their evidence and the returned probability, when available.
Wrong ticker or snapshot in a result	The coordinator rejects the mismatch and identifies it in the log.
Scanner returns the same capture ID twice	The coordinator rejects the duplicate before a chart assessment starts.
Evidence or tool unavailable	The log records the missing evidence or tool error.
Replay cutoff advances	Each assessment identifies its own cutoff and uses only data available at that time.
Stop during a run	No new work starts. Late results are labeled as belonging to the stopped run.
Session limit reached	Monitoring stops even if additional watchlist candidates remain.
Use labeled test results to verify missing-data, duplicate, and stopping behavior. Then run the agents on recorded charts and compare their actual results with the intended conditions. Check both the coordination and the accuracy of each chart assessment, as in Activity 4.

↩ Module VII — Assignment 4: Live Market Workstation

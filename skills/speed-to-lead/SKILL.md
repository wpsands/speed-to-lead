---
name: speed-to-lead
description: Runs a Speed-to-Lead Test on a B2B SaaS demo funnel, either the user's own (a self-diagnostic) or one prospect's. Submits a demo request through the real form, logs every email, call, voicemail, SMS and LinkedIn touch for seven days, then scores the response on speed, persistence, channels, personalization and path to a meeting and writes a scorecard. Use when the user says "speed to lead test", "test our demo form", "how fast do we respond to demo requests", "test {company}'s demo response", "how fast does {company} respond", "check the speed-to-lead run", or "score the speed-to-lead test".
---

# Speed-to-Lead Test

Measure how fast and how well a company responds when a buyer asks for a demo. Every number comes from logged evidence: a submission time, and a timestamped touch with its source.

## Load the method

Read `${CLAUDE_PLUGIN_ROOT}/method/rubric.md` before starting. Follow its definitions and its rules for a fair test throughout.

The CLI is `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/stl.py`. Below it is written as `stl`.

## Inputs

Ask once, only for what is missing:

1. **Mode.** `self` (the user's own funnel) or `prospect` (another company). The rubric's rules differ by mode; follow the matching set.
2. **Company.** Name, domain, and demo form URL. Default to one company. Score several only when the user asks.
3. **Buyer details.** In `prospect` mode, the user's real name, work email, title and company. In `self` mode, a realistic test buyer whose inbox and phone the user controls. In both, the phone number for the form (the Twilio number if one is set up).
4. **The question.** One question a real buyer would ask.
5. **Run folder.** Default `./runs/<company>-<date>`. Run folders hold real data and stay out of git.

In `self` mode, confirm the user has agreement from the leader who owns inbound response before submitting.

## Workflow

### 0. Check coverage
Test what this run will use and state the result before going on:
- Email: a Gmail connector signed in as the buyer's work email. Confirm with one metadata-only search.
- Browser: the browser tool available in this session, for filling forms.
- Phone: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and `TWILIO_NUMBER` in the environment. Load them with the user's secret manager (for example `hq secrets exec`); never ask the user to paste a token into chat. If they are missing, phone touches are logged by hand and the report says so.
- LinkedIn: always logged by hand.

### 1. Set up the run
```
stl init RUN --name "<company> <mode> test"
stl add-company RUN --name "<Company>" --domain <domain> --form-url <url>
```

### 2. Submit, one company at a time
1. Open the form in the browser. Fill it with the buyer details and the question.
2. Count the visible fields and note whether a phone number is required.
3. Stop before the submit button and ask the user to review and click it. Do not submit for them.
4. Record the moment they confirm, and whether a scheduler appeared right after submit:
   ```
   stl submit RUN --company <id> --fields <n> [--phone-required] [--scheduler instant]
   ```
5. If the scheduler offered a booking, do not book it.

### 3. Collect, once a day during the window
**Email.** Search the inbox for each company's domain since its request time, for example `from:<domain> after:YYYY/MM/DD`. For each message decide:
- `kind`: `auto` for confirmations, receipts and no-reply senders; `human` for a named person writing about this request. Skip newsletters and event invitations entirely.
- `personalization`: 0 template, 1 names the company or role, 2 answers the question asked.
- `meeting`: `times`, `link`, `questions` or `none`.

Write the results to a JSON list and import it. Use the message id as `ref` so a second import skips it:
```json
[{"company": "acme", "channel": "email", "at": "2026-11-02T14:11:00Z", "kind": "human",
  "personalization": 2, "meeting": "times", "from": "jo@acme.com", "summary": "Answered the SSO question, offered Tue/Wed", "ref": "<message id>"}]
```
```
stl import RUN touches.json
```
Read only. Never reply, label or archive in the user's inbox unless they ask.

**Phone and SMS.**
```
stl twilio-pull RUN
```
Callers whose number is not mapped go to the unmatched queue. Identify the company from the voicemail, the text body or the caller ID, then:
```
stl map-number RUN --number +1... --company <id>
```
Set `personalization` and `meeting` on phone touches only when a voicemail or text shows them; otherwise leave the defaults.

**LinkedIn.** Ask the user whether anyone from a tested company reached out, then log each one:
```
stl touch RUN --company <id> --channel linkedin --at <ISO time> --kind human --personalization <0-2> --meeting <level>
```

Run `stl status RUN` to show which windows are open and what is unmatched.

### 4. Score and report
After the last window closes:
```
stl report RUN
```
This writes `scores.json` and `report.md` in the run folder. Present the scorecard: the timeline, the score by part, and the two or three changes that would gain the most points. If several companies were scored, also present the benchmark. Check that every touch in the report has a source; flag anything logged by hand.

### 5. Close the loop
**Self mode.** Remind the user to tell the team it was a test and to remove the test lead from the CRM and reports. Offer a one-page summary for the leader who owns inbound response: the timeline, the score, and the fixes ranked by points gained.

**Prospect mode.** Remind the user to answer every human touch now. If the user wants to use the scorecard in outreach, draft a short note to the company's revenue leader with the timeline and one fix. Draft only. The user sends it.

## Output rules
- Times in the report are measured from the recorded submit time. Never estimate a missing time; leave the touch out and say so.
- Do not publish company names from a run without the company's agreement. Benchmarks are shared in aggregate.
- Write plainly: short sentences, no em dashes, no hype.

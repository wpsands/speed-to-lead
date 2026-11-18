# Rubric

Each company is scored out of 100 on what happened in the response window (7 days by default) after one demo request. The scoring code is `scripts/stl.py`; this file and the code must agree.

## Definitions

- **Request time.** The moment the form was submitted. The clock starts here.
- **Touch.** Any message or call from the company to the buyer: email, call, voicemail, SMS, LinkedIn.
- **Human touch.** A touch written or made by a person for this buyer. Rep emails sent from a sequence tool count when they come from a named rep and refer to the request.
- **Automated touch.** A confirmation, receipt or no-reply message. Logged and reported, never scored as a response.
- **Excluded.** Newsletters, webinar invitations and marketing emails that do not refer to the request. Not logged.
- **Phone.** Calls and voicemails count as one channel.

## Points

| Part | Max | How it is scored |
|---|---|---|
| Speed | 40 | Time from request to first human touch: 5 min or less 40, 15 min 32, 1 hour 24, 4 hours 16, 24 hours 8, 72 hours 3, later or never 0 |
| Persistence | 20 | Human touches in the window: 0 is 0, 1 is 5, 2 is 10, 3 or 4 is 15, 5 or more is 20 |
| Channels | 10 | Distinct channels among human touches: 1 is 4, 2 is 7, 3 or more is 10 |
| Personalization | 15 | Best human touch: template 0, names the company or role 8, answers the question asked 15 |
| Meeting | 15 | Calendar offered on submit 15, specific times proposed 12, booking link 10, qualifying questions only 4, nothing 0 |

Grades: A 85 and up, B 70, C 55, D 40, F below 40.

## Why these weights

Speed carries the most weight because the odds of reaching and qualifying a lead fall fast in the first hour (Oldroyd, McElheran and Elkington, "The Short Life of Online Sales Leads," Harvard Business Review, March 2011). Persistence and channels reward a follow-up process instead of one lucky reply. Personalization separates a rep who read the request from a sequence that fired. The meeting score rewards the shortest path to a conversation, which is what the buyer asked for.

## Recorded, not scored

- Form fields and whether a phone number was required.
- Time to the automated confirmation.
- Whether the request was sent inside the company's business hours. Read speed results with this in mind; the report shows request times.

## Rules for a fair test

### Testing your own funnel

1. Get agreement from the leader who owns inbound response. Do not tell the reps or the SDR manager; a test they know about measures nothing.
2. Use a realistic test buyer: a believable name, title and company, an email inbox and phone number you control, and a question a real prospect would ask. Match the segment you most want to win.
3. Do not reply during the window.
4. After the window closes, tell the team it was a test, share the scorecard with the people who responded, and remove the test lead from the CRM and reports.

### Testing another company

1. One request per company, under your real name, work email and title, with a question you would actually want answered.
2. Use it sparingly, on a company you are already talking to or plan to approach. It is a way to start a conversation, not a volume tactic.
3. Do not reply during the window. A reply changes what the company does next.
4. After the window closes, answer every human touch: take the meeting if you are interested, or thank them and say you were looking at how their inbound process works.
5. Keep company names and results out of anything public unless the company agrees. If you score several companies, publish only in aggregate.
